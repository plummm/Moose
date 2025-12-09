"""Command to run test suites."""

import argparse
import sys
import subprocess
import os
from pathlib import Path
try:
    from moose.framework.logging import init_core_logger, get_core_logger
except ImportError:
    # Fallback for development mode
    from framework.logging import init_core_logger, get_core_logger


class TestCommand:
    """Command to run test suites for framework components."""
    
    def custom_subparser(self, subparser, cmd):
        """Create and return a subparser for the test command."""
        return subparser.add_parser(cmd, help='Run test suites')
    
    def add_arguments(self, parser):
        """Add arguments to the test command parser."""
        parser.add_argument(
            'component',
            type=str,
            choices=['llm_core', 'agent_core', 'all'],
            help='Component to test (llm_core, agent_core, or all)'
        )
        parser.add_argument(
            '--verbose', '-v',
            action='store_true',
            help='Verbose output'
        )
        parser.add_argument(
            '--coverage',
            action='store_true',
            help='Generate coverage report'
        )
    
    def run(self, args):
        """Execute the test command."""
        debug = getattr(args, 'debug', False)
        logger = init_core_logger(debug=debug)
        logger.info(f"Running tests for component: {args.component}")
        
        # Get test directory (moose/tests/)
        moose_dir = Path(__file__).parent.parent.parent
        tests_dir = moose_dir / "tests"
        
        # Also check if we're in the root directory
        root_tests_dir = Path.cwd() / "moose" / "tests"
        if not tests_dir.exists() and root_tests_dir.exists():
            tests_dir = root_tests_dir
        
        if not tests_dir.exists():
            logger.error(f"Tests directory not found: {tests_dir}")
            return 1
        
        # Determine which tests to run
        test_files = []
        if args.component == "llm_core" or args.component == "all":
            test_file = tests_dir / "test_llm_core.py"
            if test_file.exists():
                test_files.append(str(test_file))
            else:
                logger.warning(f"Test file not found: {test_file}")
        
        if args.component == "agent_core" or args.component == "all":
            test_file = tests_dir / "test_agent_core.py"
            if test_file.exists():
                test_files.append(str(test_file))
            else:
                logger.warning(f"Test file not found: {test_file}")
        
        if not test_files:
            logger.error("No test files found to run")
            return 1
        
        # Build pytest arguments
        pytest_args = []
        
        # Add test files
        pytest_args.extend(test_files)
        
        # Add verbose flag
        if args.verbose:
            pytest_args.append("-v")
        else:
            pytest_args.append("-q")  # Quiet mode
        
        # Add coverage if requested
        if args.coverage:
            pytest_args.extend(["--cov=framework.llm_core", "--cov=framework.agent_core"])
            pytest_args.append("--cov-report=html")
            pytest_args.append("--cov-report=term")
        
        # Add markers for docker tests
        if args.component == "agent_core":
            # For agent_core, include docker tests
            # Don't filter - let pytest handle skipping if docker unavailable
            pass
        elif args.component == "llm_core":
            # For llm_core, skip docker tests (they're marked with @pytest.mark.docker)
            pytest_args.extend(["-m", "not docker"])
        else:
            # For all, include all tests
            pass
        
        # Determine working directory (should be project root)
        work_dir = Path.cwd()
        if (work_dir / "moose").exists():
            work_dir = work_dir
        elif moose_dir.parent.exists():
            work_dir = moose_dir.parent
        else:
            work_dir = moose_dir
        
        # If --debug-mode flag, run pytest directly (for VSCode debugging)
        if getattr(args, 'debug', False):
            try:
                import pytest
                logger.info("Running pytest directly (debug mode)")
                logger.info(f"Working directory: {work_dir}")
                
                # Convert test file paths to be relative to work_dir for better compatibility
                debug_pytest_args = []
                for arg in pytest_args:
                    if arg.endswith('.py') and os.path.isabs(arg):
                        # Convert absolute path to relative path from work_dir
                        try:
                            rel_path = os.path.relpath(arg, work_dir)
                            debug_pytest_args.append(rel_path)
                        except ValueError:
                            # If paths are on different drives (Windows), use absolute path
                            debug_pytest_args.append(arg)
                    else:
                        debug_pytest_args.append(arg)
                
                logger.info(f"Pytest args: {' '.join(debug_pytest_args)}")
                
                # Change to working directory and run pytest
                original_cwd = os.getcwd()
                try:
                    os.chdir(str(work_dir))
                    exit_code = pytest.main(debug_pytest_args)
                    return exit_code
                finally:
                    os.chdir(original_cwd)
                    
            except ImportError:
                logger.error("pytest not found. Please install pytest: pip install pytest")
                return 1
            except Exception as e:
                logger.error(f"Failed to run tests in debug mode: {e}")
                return 1
        
        else:
        
            # Otherwise, use subprocess (normal execution)
            pytest_cmd = ["python", "-m", "pytest"] + pytest_args
            logger.info(f"Running pytest via subprocess: {' '.join(pytest_cmd)}")
            
            # Run tests
            try:
                result = subprocess.run(
                    pytest_cmd,
                    cwd=str(work_dir),
                    check=False
                )
                
                if result.returncode == 0:
                    logger.info("All tests passed!")
                else:
                    logger.warning(f"Some tests failed (exit code: {result.returncode})")
                
                return result.returncode
                
            except FileNotFoundError:
                logger.error(
                    "pytest not found. Please install pytest: pip install pytest"
                )
                return 1
            except Exception as e:
                logger.error(f"Failed to run tests: {e}")
                return 1

