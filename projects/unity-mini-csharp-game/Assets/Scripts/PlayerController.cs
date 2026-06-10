using UnityEngine;

public sealed class PlayerController : MonoBehaviour
{
    [SerializeField] private float moveSpeed = 6f;

    private Rigidbody2D body;
    private Vector2 moveInput;

    private void Awake()
    {
        body = GetComponent<Rigidbody2D>();
    }

    private void Update()
    {
        moveInput = new Vector2(Input.GetAxisRaw("Horizontal"), Input.GetAxisRaw("Vertical"));
        moveInput = Vector2.ClampMagnitude(moveInput, 1f);
    }

    private void FixedUpdate()
    {
        body.velocity = moveInput * moveSpeed;
    }

    private void OnTriggerEnter2D(Collider2D other)
    {
        if (other.TryGetComponent(out Coin coin))
        {
            coin.Collect();
            return;
        }

        if (other.TryGetComponent(out EnemyPatrol _))
        {
            GameManager.Instance.LoseGame();
        }
    }
}

