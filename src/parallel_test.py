import asyncio
from dotenv import load_dotenv
import anthropic

load_dotenv()
client = anthropic.AsyncAnthropic()

async def ask(n):
    r = await client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=30,
        messages=[{"role": "user", "content": f"Reply with only the number {n}"}],
    )
    return r.content[0].text

async def main():
    results = await asyncio.gather(*(ask(i) for i in range(3)))
    print(results)

asyncio.run(main())
