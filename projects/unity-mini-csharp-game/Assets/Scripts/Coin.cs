using UnityEngine;

public sealed class Coin : MonoBehaviour
{
    [SerializeField] private int points = 1;

    public void Collect()
    {
        GameManager.Instance.AddScore(points);
        Destroy(gameObject);
    }
}

