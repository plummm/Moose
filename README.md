# Moose Framework

A modular agent framework built on LangGraph for creating customizable agent workflows.

## Installation

1. Clone or download this repository
2. Install dependencies:
   ```bash
   pip install langgraph
   ```

## Setup

Set the `MOOSE_PROJECTS_DIR` environment variable to specify where your projects will be stored:

```bash
export MOOSE_PROJECTS_DIR=/path/to/your/projects/dir
```

Or add it to your shell profile (`.bashrc`, `.zshrc`, etc.):

```bash
echo 'export MOOSE_PROJECTS_DIR=/path/to/your/projects/dir' >> ~/.zshrc
source ~/.zshrc
```

## Usage

### Create a New Project

```bash
python moose/moose.py create <project_name>
```

Example:
```bash
python moose/moose.py create my_trading_bot
```

This will create a new project directory at `{MOOSE_PROJECTS_DIR}/my_trading_bot/` with:
- `project_config.json` - Project configuration file
- `workflow.py` - LangGraph workflow definition template

## Project Structure

Each project contains:
- `project_config.json` - Configuration for the project
- `workflow.py` - LangGraph workflow definition where you define your agent graph

## Development

The framework uses a modular command system. Commands are located in `framework/commands/` and follow a class-based pattern.

