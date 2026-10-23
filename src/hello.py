from dotenv import load_dotenv
import anthropic

load_dotenv()  # reads ANTHROPIC_API_KEY from .env
client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-haiku-5-5",
    max_tokens=100,
    messages=[{"role": "user", "content": "Reply with exactly: setup works"}],
)
print(response.content[0].text)
