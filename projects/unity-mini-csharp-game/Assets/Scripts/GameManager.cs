using UnityEngine;
using UnityEngine.SceneManagement;

public sealed class GameManager : MonoBehaviour
{
    public static GameManager Instance { get; private set; }

    [SerializeField] private PlayerController player;

    private int totalCoins;
    private int score;
    private bool gameEnded;

    private void Awake()
    {
        if (Instance != null && Instance != this)
        {
            Destroy(gameObject);
            return;
        }

        Instance = this;
    }

    private void Start()
    {
        totalCoins = FindObjectsOfType<Coin>().Length;
        Debug.Log($"Coin Dash iniciado. Moedas na cena: {totalCoins}");
    }

    private void Update()
    {
        if (gameEnded && Input.GetKeyDown(KeyCode.R))
        {
            SceneManager.LoadScene(SceneManager.GetActiveScene().buildIndex);
        }
    }

    public void AddScore(int points)
    {
        if (gameEnded)
        {
            return;
        }

        score += points;
        Debug.Log($"Pontuação: {score}/{totalCoins}");

        if (score >= totalCoins)
        {
            WinGame();
        }
    }

    public void LoseGame()
    {
        if (gameEnded)
        {
            return;
        }

        gameEnded = true;
        SetPlayerEnabled(false);
        Debug.Log("Você perdeu! Aperte R para reiniciar.");
    }

    private void WinGame()
    {
        gameEnded = true;
        SetPlayerEnabled(false);
        Debug.Log("Você venceu! Aperte R para jogar novamente.");
    }

    private void SetPlayerEnabled(bool enabled)
    {
        if (player != null)
        {
            player.enabled = enabled;
        }
    }
}
