"""Example usage of the LLM Core module."""

# This file demonstrates how to use the universal LLM client

from framework.llm_core import LLMClient, Message, MessageRole, LLMProvider


def example_basic_usage():
    """Basic example: send a simple message."""
    print("=== Basic Usage ===")
    
    # Initialize client (provider auto-detected from model name)
    client = LLMClient(model="gpt-4")
    
    # Send a message
    response = client.send_message("What is 2+2?")
    print(f"Response: {response.content}")
    print(f"Model: {response.model}")
    print(f"Tokens used: {response.usage}")


def example_with_system_message():
    """Example with system message."""
    print("\n=== With System Message ===")
    
    client = LLMClient(model="claude-3-opus")
    
    response = client.send_message(
        message="What is the capital of France?",
        system_message="You are a helpful geography assistant."
    )
    print(f"Response: {response.content}")


def example_conversation():
    """Example with conversation history."""
    print("\n=== Conversation History ===")
    
    client = LLMClient(model="gemini-pro")
    
    # First message
    response1 = client.send_message("My name is Alice and I like Python programming")
    print(f"Assistant: {response1.content}")
    
    # Continue conversation
    messages = [
        Message(role=MessageRole.USER, content="My name is Alice and I like Python programming"),
        Message(role=MessageRole.ASSISTANT, content=response1.content)
    ]
    
    response2 = client.send_message(
        message="What's my name and what do I like?",
        messages=messages
    )
    print(f"Assistant: {response2.content}")


def example_streaming():
    """Example of streaming responses."""
    print("\n=== Streaming Response ===")
    
    client = LLMClient(model="gpt-4")
    
    print("Streaming response: ", end="", flush=True)
    for chunk in client.stream_message("Count from 1 to 5"):
        print(chunk, end="", flush=True)
    print()  # New line after streaming


def example_multiple_providers():
    """Example showing the same API works across providers."""
    print("\n=== Multiple Providers (Same API) ===")
    
    # All use the same interface!
    openai_client = LLMClient(model="gpt-4")
    claude_client = LLMClient(model="claude-3-opus")
    gemini_client = LLMClient(model="gemini-pro")
    
    question = "What is the meaning of life?"
    
    print("OpenAI GPT-4:")
    response1 = openai_client.send_message(question)
    print(f"  {response1.content[:100]}...")
    
    print("\nAnthropic Claude:")
    response2 = claude_client.send_message(question)
    print(f"  {response2.content[:100]}...")
    
    print("\nGoogle Gemini:")
    response3 = gemini_client.send_message(question)
    print(f"  {response3.content[:100]}...")


def example_explicit_provider():
    """Example with explicit provider specification."""
    print("\n=== Explicit Provider ===")
    
    client = LLMClient(
        model="claude-3-opus",
        provider=LLMProvider.ANTHROPIC,
        temperature=0.7,
        max_tokens=100
    )
    
    response = client.send_message("Hello!")
    print(f"Response: {response.content}")


if __name__ == "__main__":
    # Note: These examples require API keys to be set in environment variables
    # export OPENAI_API_KEY="your-key"
    # export ANTHROPIC_API_KEY="your-key"
    # export GOOGLE_API_KEY="your-key"
    
    try:
        example_basic_usage()
        example_with_system_message()
        example_conversation()
        example_streaming()
        example_multiple_providers()
        example_explicit_provider()
    except ImportError:
        print("Error: Please install litellm: pip install litellm")
    except Exception as e:
        print(f"Error: {e}")
        print("Make sure you have set the appropriate API keys in your environment.")

