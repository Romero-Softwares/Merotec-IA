import speech_recognition as sr
import pyttsx3
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
from scipy import signal
import tempfile
import os
import threading
import time
import unicodedata
import re
import difflib
from queue import Empty, Queue


def _normalizar_texto_para_fala(text):
    texto = str(text or "")
    texto = re.sub(r"```.*?```", " ", texto, flags=re.DOTALL)
    texto = re.sub(
        r"\[(?:READ|WRITE|REPLACE|SEARCH_TEXT|SCAN_TEXT|FIX_MOJIBAKE|EXECUTE|OPEN_URL|SCREENSHOT|HUMAN_TEST|UNDO):[^\]]+\]",
        " ",
        texto,
    )
    texto = texto.replace("```", " ")
    texto = re.sub(r"[*_`>#|\[\]{}]", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()

class VoiceModule:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8
        self.recognizer.non_speaking_duration = 0.4
        self.fs = 16000
        self.input_device = None
        self.input_samplerate = self.fs
        self.active_recording_samplerate = self.fs
        self.record_seconds = 7
        self.recording_stream = None
        self.recording_chunks = []
        self.recording_lock = threading.Lock()
        self.microphone_lock = threading.Lock()
        self.recording_has_microphone_lock = False
        self.is_recording = False
        self.is_paused = False
        self.stop_requested = False
        self.keyword_listener_active = False
        self.keyword_listener_thread = None
        self.keyword_stop_event = threading.Event()
        self.keyword_start_aliases = (
            "merotec",
            "merotek",
            "berotec",
            "berotek",
            "mero tech",
            "merotech",
            "mero tec",
            "mero tek",
            "meroteque",
            "meroteck",
            "merito",
            "meritoc",
            "me rotc",
            "meu roteque",
            "meu rotec",
            "meu rotek",
            "mero",
        )
        self.keyword_end_aliases = (
            "ok",
            "okay",
            "okey",
            "oquei",
            "certo",
            "finalizar",
            "ok merotec",
            "ok mérito",
            "pronto",
        )
        self.keyword_capture_preroll_seconds = 0.8
        self.keyword_silence_seconds = 2.2
        self.keyword_min_command_seconds = 1.4
        self.queue = Queue()

        # Processo auxiliar que gerencia o motor de voz sem conflitos
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        """Processo auxiliar simplificado para gerenciar pedidos de fala"""
        while True:
            texto_completo = self.queue.get()
            if texto_completo is None: break

            # Criamos uma instância nova e única para esta leitura específica
            # Isso garante que ele leia o texto do início ao fim sem travar
            engine_local = pyttsx3.init()
            engine_local.setProperty('rate', 180)

            try:
                # Divide em parágrafos para maior estabilidade
                paragrafos = texto_completo.split('\n')
                for p in paragrafos:
                    if self.stop_requested: break

                    # Lógica de Pausa
                    while self.is_paused and not self.stop_requested:
                        time.sleep(0.1)

                    if p.strip():
                        engine_local.say(p.strip())
                        engine_local.runAndWait()
            except Exception as e:
                print(f"Erro na reprodução: {e}")
            finally:
                # Limpa a instância após o término
                del engine_local
                self.queue.task_done()

    def speak(self, text):
        """Lê o texto completo reconstruindo o motor para evitar travamentos"""

        def run_speech():
            self.stop_requested = False
            try:
                # Criamos o motor dentro da thread de forma isolada
                engine = pyttsx3.init()
                engine.setProperty('rate', 180)

                # Limpamos o texto de caracteres que fazem o motor parar cedo
                texto_limpo = _normalizar_texto_para_fala(text)

                # Dividimos apenas em partes grandes (parágrafos)
                partes = [texto_limpo] if texto_limpo else []

                for parte in partes:
                    if self.stop_requested:
                        break

                    # Lógica de Pausa
                    while self.is_paused and not self.stop_requested:
                        time.sleep(0.1)

                    if parte.strip():
                        engine.say(parte.strip())
                        engine.runAndWait()  # Força a leitura desta parte

            except Exception as e:
                print(f"Erro na fala: {e}")
            finally:
                # Garante que o motor seja liberado
                try:
                    engine.stop()
                except:
                    pass

        # Inicia a thread de fala
        threading.Thread(target=run_speech, daemon=True).start()

    def stop(self):
        """Interrompe a fala atual imediatamente"""
        self.stop_requested = True
        # Força o esvaziamento da fila
        with self.queue.mutex:
            self.queue.queue.clear()

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    def _get_input_device_config(self):
        if self.input_device is not None:
            return self.input_device, self.input_samplerate

        devices = sd.query_devices()
        default_input = sd.default.device[0] if sd.default.device else None
        candidate_indexes = []
        if isinstance(default_input, int) and default_input >= 0:
            candidate_indexes.append(default_input)
        candidate_indexes.extend(
            index for index, device in enumerate(devices)
            if device.get("max_input_channels", 0) > 0 and index not in candidate_indexes
        )

        for index in candidate_indexes:
            device = devices[index]
            if device.get("max_input_channels", 0) <= 0:
                continue
            samplerate = int(device.get("default_samplerate") or self.fs)
            self.input_device = index
            self.input_samplerate = samplerate
            return self.input_device, self.input_samplerate

        raise RuntimeError("Nenhum microfone/dispositivo de entrada foi encontrado pelo sistema.")

    def _record_input_chunk(self, samples):
        device, samplerate = self._get_input_device_config()
        return sd.rec(
            samples,
            samplerate=samplerate,
            channels=1,
            dtype="int16",
            device=device,
        )

    def _prepare_recording_for_transcription(self, recording, samplerate, aggressive=False):
        if recording is None or len(recording) == 0:
            return None

        audio = np.asarray(recording, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return None

        if np.max(np.abs(audio)) > 1.5:
            audio = audio / 32768.0

        audio = np.nan_to_num(audio)
        audio = audio - float(np.mean(audio))

        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < 0.0025:
            return None

        nyquist = max(1.0, samplerate / 2.0)
        low = min(70.0 / nyquist, 0.95)
        high = min(4200.0 / nyquist, 0.95)
        if 0 < low < high < 1:
            try:
                sos = signal.butter(3, [low, high], btype="bandpass", output="sos")
                audio = signal.sosfiltfilt(sos, audio).astype(np.float32)
            except Exception:
                pass

        if samplerate != self.fs and samplerate > 0:
            try:
                target_size = max(1, int(audio.size * self.fs / samplerate))
                audio = signal.resample_poly(audio, self.fs, samplerate).astype(np.float32)
                if audio.size > target_size:
                    audio = audio[:target_size]
                samplerate = self.fs
            except Exception:
                pass

        frame = max(1, int(samplerate * 0.02))
        if audio.size >= frame * 4:
            usable = audio[: min(audio.size, int(samplerate * 1.5))]
            frames = usable[: (usable.size // frame) * frame].reshape(-1, frame)
            noise_floor = float(np.percentile(np.sqrt(np.mean(frames * frames, axis=1)), 25)) if frames.size else 0.0
            gate_multiplier = 2.2 if aggressive else 1.35
            minimum_gate = 0.0045 if aggressive else 0.0025
            reduction = 0.16 if aggressive else 0.45
            gate = max(noise_floor * gate_multiplier, minimum_gate)
            envelope = np.convolve(np.abs(audio), np.ones(frame, dtype=np.float32) / frame, mode="same")
            audio = np.where(envelope >= gate, audio, audio * reduction)

        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < 0.0035:
            return None

        audio = np.clip(audio / peak * 0.92, -1.0, 1.0)
        return (audio * 32767).astype(np.int16).reshape(-1, 1)

    def _prepare_direct_recording_for_wav(self, recording):
        if recording is None or len(recording) == 0:
            return None

        audio = np.asarray(recording)
        if audio.size == 0:
            return None

        if audio.ndim > 1:
            audio = audio[:, :1]
        else:
            audio = audio.reshape(-1, 1)

        if audio.dtype == np.int16:
            return audio

        audio = np.asarray(audio, dtype=np.float32)
        audio = np.nan_to_num(audio)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak <= 0:
            return None
        if peak <= 1.5:
            audio = np.clip(audio, -1.0, 1.0) * 32767
        else:
            audio = np.clip(audio, -32768, 32767)
        return audio.astype(np.int16)

    def _recognize_wav_array(self, audio, samplerate, keywords=()):
        temp_filename = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_wav:
                temp_filename = temp_wav.name
                wav.write(temp_filename, int(samplerate), audio)

            with sr.AudioFile(temp_filename) as source:
                audio_data = self.recognizer.record(source)
                return self._recognize_google_best(audio_data, keywords=keywords)
        finally:
            if temp_filename and os.path.exists(temp_filename):
                os.remove(temp_filename)

    def _write_temp_wav_and_recognize(self, recording, samplerate, keywords=(), aggressive=False):
        candidates = []
        direct = self._prepare_direct_recording_for_wav(recording)
        if direct is not None:
            candidates.append((direct, int(samplerate or self.input_samplerate or self.fs)))

        clean = self._prepare_recording_for_transcription(recording, samplerate, aggressive=False)
        if clean is not None:
            candidates.append((clean, self.fs))

        if aggressive:
            filtered = self._prepare_recording_for_transcription(recording, samplerate, aggressive=True)
            if filtered is not None:
                candidates.append((filtered, self.fs))

        if not candidates:
            return None

        last_unknown = None
        for audio, candidate_samplerate in candidates:
            try:
                transcript = self._recognize_wav_array(audio, candidate_samplerate, keywords=keywords)
                if transcript:
                    return transcript
            except sr.UnknownValueError as exc:
                last_unknown = exc
                continue

        if last_unknown is not None:
            raise last_unknown
        return None

    def _audio_has_voice(self, recording, samplerate):
        audio = np.asarray(recording, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return False
        if np.max(np.abs(audio)) > 1.5:
            audio = audio / 32768.0
        audio = np.nan_to_num(audio)
        audio = audio - float(np.mean(audio))
        frame = max(1, int(samplerate * 0.03))
        if audio.size < frame:
            return False
        frames = audio[: (audio.size // frame) * frame].reshape(-1, frame)
        rms = np.sqrt(np.mean(frames * frames, axis=1))
        if rms.size == 0:
            return False
        floor = float(np.percentile(rms, 30))
        active = rms > max(floor * 2.5, 0.006)
        return bool(np.mean(active) >= 0.12 or float(np.max(rms)) >= 0.018)

    def _recognize_google_best(self, audio_data, keywords=()):
        result = self.recognizer.recognize_google(audio_data, language="pt-BR", show_all=True)
        alternatives = []
        if isinstance(result, dict):
            alternatives = result.get("alternative") or []
        if alternatives:
            keyword_terms = tuple(self.normalize_command_text(term) for term in keywords if term)

            def score(item):
                transcript = item.get("transcript", "")
                normalized = self.normalize_command_text(transcript)
                confidence = float(item.get("confidence", 0.0) or 0.0)
                keyword_bonus = 0.0
                if keyword_terms:
                    keyword_bonus = max(
                        (1.0 if term and term in normalized else difflib.SequenceMatcher(None, term, normalized[: max(len(term), 12)]).ratio())
                        for term in keyword_terms
                    )
                return (keyword_bonus, confidence, len(normalized))

            best = max(alternatives, key=score)
            transcript = best.get("transcript", "").strip()
            if transcript:
                return transcript

        return self.recognizer.recognize_google(audio_data, language="pt-BR")

    def start_recording(self):
        if self.is_recording:
            return

        if self.keyword_listener_active:
            self.stop_keyword_listener()

        self.microphone_lock.acquire()
        self.recording_has_microphone_lock = True

        def callback(indata, frames, time_info, status):
            with self.recording_lock:
                self.recording_chunks.append(indata.copy())

        try:
            with self.recording_lock:
                self.recording_chunks = []
            device, samplerate = self._get_input_device_config()
            self.active_recording_samplerate = samplerate
            self.recording_stream = sd.InputStream(
                samplerate=samplerate,
                channels=1,
                dtype="int16",
                device=device,
                callback=callback,
            )
            self.recording_stream.start()
            self.is_recording = True
        except Exception:
            self.recording_stream = None
            self.is_recording = False
            self.recording_has_microphone_lock = False
            self.microphone_lock.release()
            raise

    def stop_recording_and_transcribe(self):
        if not self.is_recording:
            return None
        stream = self.recording_stream
        self.recording_stream = None
        self.is_recording = False

        try:
            if stream:
                stream.stop()
                stream.close()
        finally:
            with self.recording_lock:
                chunks = list(self.recording_chunks)
                self.recording_chunks = []
            if self.recording_has_microphone_lock:
                self.recording_has_microphone_lock = False
                self.microphone_lock.release()

        if not chunks:
            return None

        recording = np.concatenate(chunks, axis=0)
        try:
            transcript = self._write_temp_wav_and_recognize(
                recording,
                self.active_recording_samplerate,
                keywords=self.keyword_start_aliases + self.keyword_end_aliases,
                aggressive=True,
            )
            if not transcript:
                return None
            command, finished = self.extract_keyword_command(transcript)
            return command if command and finished else transcript
        except sr.UnknownValueError:
            return None

    def normalize_command_text(self, text):
        normalized = unicodedata.normalize("NFD", text or "")
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        return " ".join(normalized.lower().strip().split())

    def _keyword_aliases(self, keyword, default_aliases):
        normalized_keyword = self.normalize_command_text(keyword)
        aliases = [normalized_keyword]
        aliases.extend(self.normalize_command_text(alias) for alias in default_aliases)
        return tuple(dict.fromkeys(alias for alias in aliases if alias))

    def _find_keyword_span(self, text, aliases, start=0):
        best = None
        for alias in aliases:
            pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in alias.split()) + r"(?!\w)"
            match = re.search(pattern, text[start:])
            if match is None:
                continue
            index = start + match.start()
            span = (index, start + match.end())
            if best is None or span[0] < best[0] or (span[0] == best[0] and span[1] > best[1]):
                best = span
        if best is not None:
            return best

        tokens = [(match.group(0), start + match.start(), start + match.end()) for match in re.finditer(r"\S+", text[start:])]
        for alias in aliases:
            alias_tokens = alias.split()
            if not alias_tokens or len("".join(alias_tokens)) < 5:
                continue
            window_size = len(alias_tokens)
            for index in range(0, max(0, len(tokens) - window_size + 1)):
                window = tokens[index:index + window_size]
                candidate = " ".join(token[0] for token in window)
                similarity = difflib.SequenceMatcher(None, alias, candidate).ratio()
                if similarity < 0.78:
                    continue
                span = (window[0][1], window[-1][2])
                if best is None or span[0] < best[0] or (span[0] == best[0] and span[1] > best[1]):
                    best = span
        return best

    def extract_keyword_command(self, text, start_keyword="merotec", end_keyword="ok"):
        normalized = self.normalize_command_text(text)
        start_aliases = self._keyword_aliases(start_keyword, self.keyword_start_aliases)
        end_aliases = self._keyword_aliases(end_keyword, self.keyword_end_aliases)

        start_span = self._find_keyword_span(normalized, start_aliases)
        if start_span is None:
            return None, False

        command_start = start_span[1]
        end_span = self._find_keyword_span(normalized, end_aliases, command_start)
        if end_span is None:
            partial = normalized[command_start:].strip()
            return partial or None, False

        command = normalized[command_start:end_span[0]].strip()
        return command or None, True

    def has_start_keyword(self, text, start_keyword="merotec"):
        normalized = self.normalize_command_text(text)
        start_aliases = self._keyword_aliases(start_keyword, self.keyword_start_aliases)
        return self._find_keyword_span(normalized, start_aliases) is not None

    def transcribe_audio_array(self, recording, samplerate=None, aggressive=False):
        if recording is None or len(recording) == 0:
            return None
        effective_samplerate = int(samplerate or self.input_samplerate)
        prepared = self._prepare_recording_for_transcription(recording, effective_samplerate)
        if prepared is None:
            return None
        try:
            keywords = self.keyword_start_aliases + self.keyword_end_aliases
            return self._write_temp_wav_and_recognize(recording, effective_samplerate, keywords=keywords, aggressive=aggressive)
        except sr.UnknownValueError:
            return None

    def start_keyword_listener(
        self,
        on_command,
        on_capture_state=None,
        start_keyword="merotec",
        end_keyword="ok",
        window_seconds=2.5,
        max_command_seconds=25,
    ):
        if self.keyword_listener_active:
            return False
        if self.is_recording:
            return False

        device, samplerate = self._get_input_device_config()
        self.keyword_stop_event.clear()
        self.keyword_listener_active = True

        def run():
            microphone_locked = False
            listening_command = False
            command_started_at = None
            buffered_audio = []
            recent_chunks = []
            last_voice_at = None
            last_keyword_check_at = 0
            chunk_seconds = 0.35
            chunks_per_window = max(1, int(window_seconds / chunk_seconds))
            samples_per_chunk = max(1, int(samplerate * chunk_seconds))
            preroll_chunks = max(1, int(self.keyword_capture_preroll_seconds / chunk_seconds))
            stream_queue = Queue(maxsize=max(8, chunks_per_window * 3))

            def callback(indata, frames, time_info, status):
                if self.keyword_stop_event.is_set():
                    return
                if status:
                    print(f"Aviso no microfone: {status}")
                try:
                    stream_queue.put_nowait(indata.copy())
                except Exception:
                    try:
                        stream_queue.get_nowait()
                        stream_queue.put_nowait(indata.copy())
                    except Exception:
                        pass

            try:
                self.microphone_lock.acquire()
                microphone_locked = True
                with sd.InputStream(
                    samplerate=samplerate,
                    channels=1,
                    dtype="int16",
                    device=device,
                    blocksize=samples_per_chunk,
                    callback=callback,
                ):
                    while not self.keyword_stop_event.is_set():
                        try:
                            chunk = stream_queue.get(timeout=0.5)
                        except Empty:
                            continue

                        if self.keyword_stop_event.is_set() or self.is_recording:
                            continue

                        now = time.time()
                        has_voice = self._audio_has_voice(chunk, samplerate)

                        if listening_command:
                            buffered_audio.append(chunk)
                            if has_voice:
                                last_voice_at = now

                            elapsed = now - command_started_at if command_started_at else 0
                            silence_elapsed = now - last_voice_at if last_voice_at else 0
                            should_finish_by_silence = elapsed >= self.keyword_min_command_seconds and silence_elapsed >= self.keyword_silence_seconds
                            should_finish_by_timeout = elapsed >= max_command_seconds
                            if not should_finish_by_silence and not should_finish_by_timeout:
                                continue

                            recording = np.concatenate(buffered_audio, axis=0)
                            try:
                                transcript = self.transcribe_audio_array(recording, samplerate=samplerate)
                            except Exception as exc:
                                print(f"Erro ao transcrever comando de voz: {exc}")
                                transcript = None

                            command = None
                            if transcript:
                                command, finished = self.extract_keyword_command(
                                    transcript,
                                    start_keyword=start_keyword,
                                    end_keyword=end_keyword,
                                )
                                if not command:
                                    command, finished = self.extract_keyword_command(
                                        f"{start_keyword} {transcript}",
                                        start_keyword=start_keyword,
                                        end_keyword=end_keyword,
                                    )
                                if command is None:
                                    command = self.normalize_command_text(transcript)

                            buffered_audio = []
                            listening_command = False
                            command_started_at = None
                            last_voice_at = None
                            if on_capture_state:
                                on_capture_state(False)
                            if command:
                                on_command(command)
                            continue

                        recent_chunks.append(chunk)
                        if len(recent_chunks) > max(chunks_per_window, preroll_chunks):
                            recent_chunks.pop(0)
                        if len(recent_chunks) < chunks_per_window:
                            continue
                        if now - last_keyword_check_at < 1.0:
                            continue
                        last_keyword_check_at = now

                        recording = np.concatenate(recent_chunks[-chunks_per_window:], axis=0)
                        try:
                            transcript = self.transcribe_audio_array(recording, samplerate=samplerate)
                        except Exception as exc:
                            print(f"Erro ao transcrever palavra-chave: {exc}")
                            time.sleep(0.5)
                            continue

                        if not transcript:
                            continue

                        command, finished = self.extract_keyword_command(
                            transcript,
                            start_keyword=start_keyword,
                            end_keyword=end_keyword,
                        )

                        if command and finished:
                            if on_capture_state:
                                on_capture_state(True)
                                on_capture_state(False)
                            on_command(command)
                            recent_chunks = []
                            continue

                        if command or self.has_start_keyword(transcript, start_keyword):
                            buffered_audio = list(recent_chunks)
                            listening_command = True
                            command_started_at = now
                            last_voice_at = now if has_voice else None
                            recent_chunks = []
                            if on_capture_state:
                                on_capture_state(True)
            except Exception as exc:
                print(f"Erro ao manter microfone ativo para palavra-chave: {exc}")
            finally:
                self.keyword_listener_active = False
                if microphone_locked:
                    self.microphone_lock.release()
                if on_capture_state:
                    on_capture_state(False)

        self.keyword_listener_thread = threading.Thread(target=run, daemon=True)
        self.keyword_listener_thread.start()
        return True

    def stop_keyword_listener(self):
        self.keyword_stop_event.set()
        self.keyword_listener_active = False
        thread = self.keyword_listener_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)

    def listen(self):
        """Sua função original de reconhecimento de voz"""
        try:
            print("Iniciando gravação...")
            self.start_recording()
            time.sleep(self.record_seconds)
            return self.stop_recording_and_transcribe()
        except Exception as e:
            print(f"Erro na escuta: {e}")
            return None
