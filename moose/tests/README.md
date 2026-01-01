# Test Suite for Moose Framework

Comprehensive test suite for framework components with coverage reporting and conditional test execution.

## Test Architecture

```mermaid
graph TB
    subgraph "Test Execution"
        TestCmd[moose test command]
        Pytest[pytest]
    end
    
    subgraph "Test Modules"
        LLMTests[test_llm_core.py]
        AgentTests[test_agent_core.py]
        MeetRoomTests[test_meet_room.py]
        ToolRuntimeTests[test_tool_runtime.py]
        OtherTests[Other tests]
    end
    
    subgraph "External Dependencies"
        LLMProviders[LLM Providers]
        Docker[Docker Daemon]
        LangChain[LangChain]
    end
    
    subgraph "Test Markers"
        LLMMarker[@pytest.mark.llm]
        DockerMarker[@pytest.mark.docker]
        SlowMarker[@pytest.mark.slow]
    end
    
    subgraph "Coverage"
        Coverage[pytest-cov]
        HTMLReport[HTML Report]
        TerminalReport[Terminal Report]
    end
    
    TestCmd --> Pytest
    Pytest --> LLMTests
    Pytest --> AgentTests
    Pytest --> MeetRoomTests
    Pytest --> ToolRuntimeTests
    Pytest --> OtherTests
    
    LLMTests --> LLMMarker
    LLMTests --> LLMProviders
    AgentTests --> DockerMarker
    AgentTests --> Docker
    
    Pytest --> Coverage
    Coverage --> HTMLReport
    Coverage --> TerminalReport
```

## Installation

Install test dependencies:

```bash
pip install pytest pytest-cov
```

## Running Tests

### Using the Test Command

```bash
# Test LLM Core
python -m moose test llm_core

# Test Agent Core
python -m moose test agent_core

# Test all components
python -m moose test all

# Verbose output
python -m moose test llm_core --verbose

# With coverage report
python -m moose test llm_core --coverage
```

### Using pytest Directly

```bash
# Run all tests
pytest moose/tests/

# Run specific test file
pytest moose/tests/test_llm_core.py

# Run with markers
pytest -m "not docker"  # Skip docker tests
pytest -m "docker"      # Only docker tests
pytest -m "llm"         # Only LLM tests

# With coverage
pytest --cov=framework.llm_core --cov=framework.agent_core --cov-report=html
```

## Test Structure

### LLM Core Tests (`test_llm_core.py`)

Tests for LLM Core functionality:

- **Authentication Tests**: Verify API keys work for each provider
- **Message Sending**: Test sending messages to GPT-4o, Claude Sonnet 4.5, Gemini Pro
- **Conversation History**: Test multi-turn conversations
- **System Messages**: Test system prompt functionality
- **Streaming**: Test streaming responses
- **Cost Tracking**: Verify cost calculation and logging
- **Proxy Integration**: Test LiteLLM proxy functionality
- **Response Structure**: Verify response object structure

**Required Environment Variables:**
- `OPENAI_API_KEY` - For GPT-4o tests
- `ANTHROPIC_API_KEY` - For Claude tests
- `GOOGLE_API_KEY` - For Gemini tests

### Agent Core Tests (`test_agent_core.py`)

Tests for Agent Core functionality:

- **Agent Discovery**: Test agent loading and discovery
- **Configuration Loading**: Test loading agent_config.json
- **Agent Validation**: Test agent structure validation
- **Dockerfile Generation**: Test auto-generation of Dockerfiles
- **Container Building**: Test Docker image building
- **Container Lifecycle**: Test start/stop containers
- **Network Management**: Test project network creation
- **Container Logs**: Test log retrieval
- **Project Cleanup**: Test cleanup functionality

**Required:**
- Docker daemon running
- Docker installed and accessible

## Test Markers

Tests are marked with pytest markers:

- `@pytest.mark.llm` - Tests requiring LLM API keys
- `@pytest.mark.docker` - Tests requiring Docker daemon
- `@pytest.mark.slow` - Tests that take a long time

## Skipping Tests

Tests automatically skip if:
- Required API keys are not set (LLM tests)
- Docker daemon is not available (Docker tests)
- Required dependencies are missing

## Coverage

Generate coverage reports:

```bash
python -m moose test all --coverage
```

Coverage reports are generated in:
- Terminal output (summary)
- `htmlcov/` directory (HTML report)

## Writing New Tests

1. Add test methods to appropriate test file
2. Use pytest fixtures for setup/teardown
3. Mark tests with appropriate markers
4. Use `pytest.skip()` for conditional skipping
5. Follow naming convention: `test_<feature>`

Example:

```python
@pytest.mark.llm
def test_my_feature(self):
    """Test description."""
    if not self.has_openai_key:
        pytest.skip("OPENAI_API_KEY not set")
    
    # Test implementation
    assert result == expected
```

