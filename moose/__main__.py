import argparse
import os
import sys
import importlib
from pathlib import Path

commands_list = {}

# Get the directory where moose.py is located
MOOSE_DIR = Path(__file__).parent.absolute()
# Add the moose directory to Python path so we can import framework.commands
sys.path.insert(0, str(MOOSE_DIR))


def create_parser():
    parser = argparse.ArgumentParser(description='Moose Framework CLI')
    subparser = parser.add_subparsers(dest='cmd', help='sub-command help')

    command_dir = MOOSE_DIR / "framework" / "commands"
    
    if not command_dir.exists():
        print(f"Error: Command directory not found: {command_dir}")
        sys.exit(1)
    
    commands = [
        cmd[:-3] for cmd in os.listdir(command_dir)
        if cmd.endswith('.py') and not cmd == '__init__.py'
    ]
    
    for cmd in commands:
        try:
            class_name = "{}{}Command".format(cmd[0].upper(), cmd[1:])
            # Try absolute import first (when installed as package), then fallback to relative
            try:
                module = importlib.import_module("moose.framework.commands.{}".format(cmd))
            except ImportError:
                module = importlib.import_module("framework.commands.{}".format(cmd))
            new_class = getattr(module, class_name)
            A = new_class()
            parser_cmd = A.custom_subparser(subparser, cmd)
            # Add --debug to each command subparser
            parser_cmd.add_argument(
                '--debug',
                action='store_true',
                help='Enable debug logging (default: INFO level)'
            )
            A.add_arguments(parser_cmd)
            commands_list[cmd] = A
        except Exception as e:
            # Can't use logger here yet, use print
            print(f"Warning: Failed to register command '{cmd}': {e}")
    
    return parser


if __name__ == '__main__':
    parser = create_parser()
    args = parser.parse_args()
    if args.cmd in commands_list:
        commands_list[args.cmd].run(args)
    else:
        parser.print_help()
    exit(0)

