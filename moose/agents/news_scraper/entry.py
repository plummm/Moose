import os
import json
import sys
import agent

if __name__ == "__main__":
    debug = os.getenv("MOOSE_AGENT_DEBUG", "false").lower() in ("true", "1", "yes", "on")
    
    with open("./agent_config.json", 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    mode = config.get("mode")
    if mode == "http":
        port = config["http_server"].get("port", 8000)
    if mode == "file":
        watch_dir = config["file"].get("watch_dir", "/project/agent_io")

    from agent import NewsScraper
    agent = NewsScraper(config_path="./agent_config.json", debug=debug)
    
    if mode == "http":
        agent.run(mode="http", port=port)
    elif mode == "stdin":
        agent.run(mode="stdin")
    elif mode == "file":
        agent.run(mode="file", watch_dir=watch_dir)
    else:
        print("Unknown mode: " + mode, file=sys.stderr)
        sys.exit(1)