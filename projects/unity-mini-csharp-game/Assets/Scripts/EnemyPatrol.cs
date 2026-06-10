using UnityEngine;

public sealed class EnemyPatrol : MonoBehaviour
{
    [SerializeField] private Vector2 direction = Vector2.right;
    [SerializeField] private float speed = 3f;
    [SerializeField] private float distance = 4f;

    private Vector2 startPosition;

    private void Awake()
    {
        startPosition = transform.position;
        direction = direction.normalized;
    }

    private void Update()
    {
        float offset = Mathf.PingPong(Time.time * speed, distance) - distance * 0.5f;
        transform.position = startPosition + direction * offset;
    }
}

