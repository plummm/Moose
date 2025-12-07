# LLM Core - Universal LLM Interaction Layer

A unified interface for interacting with multiple LLM providers (OpenAI, Anthropic, Google, etc.) with the same API. Includes centralized cost management and load balancing through LiteLLM proxy.

## Installation

Install the required dependency:

```bash
pip install 'litellm[proxy]'
```

## Architecture

The LLM Core uses **LiteLLM Proxy** to provide:
- **Centralized Cost Management**: Automatic cost tracking for all LLM calls
- **Load Balancing**: Distribute requests across multiple model deployments
- **Rate Limiting**: Control request rates per API key or model
- **Unified API**: Same interface regardless of provider

The proxy server is automatically managed by the framework - it starts on first use and stops when the framework shuts down.

## Supported Providers

- **OpenAI** (GPT-4, GPT-3.5, etc.)
- **Anthropic** (Claude 3 Opus, Sonnet, Haiku)
- **Google** (Gemini Pro, Gemini Ultra)
- **Cohere** (Command, Command R+)
- **Mistral** (Mistral Large, Medium, Small)
- **Ollama** (Local models)
- **Azure OpenAI**
- **AWS Bedrock**

## Basic Usage

### Simple Message

```python
from framework.llm_core import LLMClient

# Initialize client (provider is auto-detected from model name)
client = LLMClient(model="gpt-4")

# Send a message
response = client.send_message("Hello, how are you?")
print(response.content)
```

### With System Message

```python
client = LLMClient(model="claude-3-opus")

response = client.send_message(
    message="What is the capital of France?",
    system_message="You are a helpful assistant."
)
print(response.content)
```

### Conversation with History

```python
from framework.llm_core import Message, MessageRole

client = LLMClient(model="gemini-pro")

# First message
response1 = client.send_message("My name is Alice")
print(response1.content)

# Continue conversation
messages = [
    Message(role=MessageRole.USER, content="My name is Alice"),
    Message(role=MessageRole.ASSISTANT, content=response1.content)
]

response2 = client.send_message(
    message="What's my name?",
    messages=messages
)
print(response2.content)
```

### Streaming Responses

```python
client = LLMClient(model="gpt-4")

for chunk in client.stream_message("Tell me a story"):
    print(chunk, end="", flush=True)
```

### Explicit Provider

```python
from framework.llm_core import LLMClient, LLMProvider

client = LLMClient(
    model="claude-3-opus",
    provider=LLMProvider.ANTHROPIC,
    api_key="your-api-key"
)
```

### Custom Parameters

```python
client = LLMClient(
    model="gpt-4",
    temperature=0.7,
    max_tokens=500,
    timeout=30.0
)

response = client.send_message("Explain quantum computing", temperature=0.5)
```

## API Reference

### LLMClient

#### `__init__(model, provider=None, api_key=None, ...)`

Initialize the LLM client.

**Parameters:**
- `model` (str): Model name (e.g., "gpt-4", "claude-3-opus")
- `provider` (LLMProvider, optional): Explicit provider. Auto-detected if None.
- `api_key` (str, optional): API key. Uses environment variables if None.
- `temperature` (float): Sampling temperature (default: 1.0)
- `max_tokens` (int, optional): Maximum tokens to generate
- `timeout` (float, optional): Request timeout in seconds

#### `send_message(message, messages=None, system_message=None, **kwargs)`

Send a message and receive a response.

**Returns:** `LLMResponse` object with:
- `content` (str): Response text
- `model` (str): Model used
- `finish_reason` (str): Reason for completion
- `usage` (dict): Token usage statistics
- `raw_response`: Raw API response

#### `send_messages(messages, system_message=None, **kwargs)`

Send multiple messages in a conversation.

#### `stream_message(message, messages=None, system_message=None, **kwargs)`

Stream response chunks as they arrive.

## Configuration

### Config File Setup

The framework uses a `config.yaml` file to configure models, costs, and proxy settings. 

1. **Create config file**: Copy the template or create `config.yaml` in your project directory:
   ```bash
   cp framework/llm_core/config.yaml.template config.yaml
   ```

2. **Customize models**: Edit `config.yaml` to add your models and set cost per token:
   ```yaml
   model_list:
     - model_name: gpt-4
       litellm_params:
         model: openai/gpt-4
       model_info:
         input_cost_per_token: 0.00003
         output_cost_per_token: 0.00006
   ```

3. **Set master key**: Change the `master_key` in `general_settings` for security.

The framework will automatically:
- Find `config.yaml` in current directory, framework directory, or project directory
- Use environment variable `LITELLM_CONFIG_PATH` to override location
- Generate default config if none found

### Environment Variables

Set API keys as environment variables:

```bash
export OPENAI_API_KEY="your-key"
export ANTHROPIC_API_KEY="your-key"
export GOOGLE_API_KEY="your-key"
export COHERE_API_KEY="your-key"
export MISTRAL_API_KEY="your-key"

# Optional: Override config file location
export LITELLM_CONFIG_PATH="/path/to/config.yaml"
```

## Cost Tracking

Cost tracking is automatic when using the proxy. Costs are logged to daily log files:

- **Location**: `llm_costs_YYYY-MM-DD.log` in current directory
- **Format**: JSON lines with timestamp, model, cost, tokens, request_id
- **Access**: Use `CostTracker` class to query costs

### Viewing Costs

```python
from framework.llm_core import CostTracker

tracker = CostTracker()

# Get today's total cost
total = tracker.get_daily_total()
print(f"Today's total: ${total:.2f}")

# Get cost for specific model
model_cost = tracker.get_model_total("gpt-4")
print(f"GPT-4 cost today: ${model_cost:.2f}")
```

### Cost in Responses

Cost is automatically included in `LLMResponse`:

```python
response = client.send_message("Hello")
print(f"Cost: ${response.cost:.6f}")
print(f"Tokens: {response.usage}")
```

## Proxy Management

The proxy is automatically managed, but you can control it manually:

```python
from framework.llm_core import ProxyManager

proxy_manager = ProxyManager.get_instance()

# Check if running
if proxy_manager.is_running():
    print("Proxy is running")
    print(f"URL: {proxy_manager.get_proxy_url()}")

# Stop proxy (usually not needed - auto-stops on exit)
# proxy_manager.stop()
```

## Disabling Proxy

To use direct API calls instead of proxy:

```python
client = LLMClient(model="gpt-4", use_proxy=False)
```

## Examples

### Switching Between Providers

```python
# Same API, different providers
openai_client = LLMClient(model="gpt-4")
claude_client = LLMClient(model="claude-3-opus")
gemini_client = LLMClient(model="gemini-pro")

# All use the same interface
response1 = openai_client.send_message("Hello")
response2 = claude_client.send_message("Hello")
response3 = gemini_client.send_message("Hello")
```

### Error Handling

```python
from framework.llm_core import LLMClient

try:
    client = LLMClient(model="gpt-4")
    response = client.send_message("Hello")
except ImportError:
    print("Please install litellm: pip install litellm")
except Exception as e:
    print(f"Error: {e}")
```

