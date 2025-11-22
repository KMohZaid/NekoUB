#!/usr/bin/env python3
"""
NekoUB Session String Generator
Generate a session string for your userbot.
"""

import asyncio
from pyrogram import Client


def print_banner():
    """Print cute banner."""
    print("\n" + "="*50)
    print("   🐱 NekoUB Session String Generator")
    print("   Nya~ Let's get your session string!")
    print("="*50 + "\n")


async def generate_session():
    """Generate session string."""
    print_banner()

    # Get API credentials
    print("📝 Please enter your Telegram API credentials")
    print("Get them from: https://my.telegram.org/apps\n")

    try:
        api_id = int(input("Enter your API_ID: "))
        api_hash = input("Enter your API_HASH: ")

        print("\n🔄 Generating session string...\n")

        # Create temporary client
        async with Client(
            "temp_session",
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True
        ) as app:
            # Get session string
            session_string = await app.export_session_string()

            print("\n" + "="*50)
            print("✅ Session string generated successfully!")
            print("="*50 + "\n")

            print("📋 Your SESSION_STRING:\n")
            print(session_string)
            print("\n" + "="*50)

            print("\n📝 Next steps:")
            print("1. Copy the session string above")
            print("2. Open your .env file")
            print("3. Paste it as: SESSION_STRING=<your_session_string>")
            print("4. Run: python main.py")
            print("\n🐱 Nya~ You're all set!\n")

    except ValueError:
        print("\n❌ Error: API_ID must be a number!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nMake sure you entered correct API credentials!")


if __name__ == "__main__":
    asyncio.run(generate_session())
