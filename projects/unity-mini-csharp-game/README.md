# Coin Dash

Mini jogo 2D jogável com duas entregas no mesmo projeto:

- `PlayableWeb/`: versão web pronta para abrir no navegador, feita com HTML, CSS e JavaScript.
- `Assets/Scripts/`: scripts C# para recriar a lógica em um projeto Unity 2D.

## Como jogar a versão web

1. Entre na pasta `PlayableWeb`.
2. Abra `index.html` diretamente no navegador ou rode um servidor local:

```bash
python -m http.server 8000
```

3. Acesse `http://127.0.0.1:8000/`.

## Objetivo

Colete moedas, evite os inimigos vermelhos e alcance a bandeira azul no fim da fase.

## Controles

- `A`/`D` ou `←`/`→`: mover.
- `W`, `↑` ou `Espaço`: pular.
- Botão `Reiniciar`: recomeçar a partida.

## Como testar no Unity

1. Crie um projeto novo no Unity usando o template `2D`.
2. Copie a pasta `Assets` deste diretório para a raiz do projeto Unity.
3. No Unity, crie uma cena vazia.
4. Crie estes objetos:
   - `Player`: adicione `SpriteRenderer`, `Rigidbody2D`, `CircleCollider2D` e o script `PlayerController`.
   - `GameManager`: adicione o script `GameManager`.
   - Moedas: objetos com `SpriteRenderer`, `CircleCollider2D` marcado como `Is Trigger` e o script `Coin`.
   - Inimigos: objetos com `SpriteRenderer`, `Rigidbody2D`, `CircleCollider2D` marcado como `Is Trigger` e o script `EnemyPatrol`.
5. No `GameManager`, arraste o `Player` para o campo `Player`.
6. Aperte `Play`.

## Arquivos principais

- `PlayableWeb/index.html`
- `PlayableWeb/style.css`
- `PlayableWeb/game.js`
- `Assets/Scripts/PlayerController.cs`
- `Assets/Scripts/Coin.cs`
- `Assets/Scripts/EnemyPatrol.cs`
- `Assets/Scripts/GameManager.cs`
