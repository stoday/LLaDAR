"""Real runtime preflight evidence for the paid acceptance fixtures."""
import json
from pathlib import Path
import subprocess


def runtime_evidence(target_python: Path) -> str:
    """Verify fixture APIs without importing or executing the fixture itself."""
    probe = subprocess.run(
        [str(target_python), '-I', '-c',
         'import json; from importlib.metadata import version; '
         'from langchain.agents import create_agent; '
         'from langchain_google_genai import ChatGoogleGenerativeAI; '
         'assert callable(create_agent) and callable(ChatGoogleGenerativeAI); '
         'print(json.dumps({"langchain": version("langchain"), '
         '"langchain-google-genai": version("langchain-google-genai")}))'],
        capture_output=True, text=True, encoding='utf-8', check=True, timeout=60,
    )
    node = subprocess.run(['node', '--version'], capture_output=True, text=True,
                          check=True, timeout=10)
    versions = json.loads(probe.stdout)
    versions['node'] = node.stdout.strip()
    return (
        'Runtime facts verified by the acceptance runner before discovery: '
        + json.dumps(versions)
        + '. In the selected target Python, imports of langchain.agents.create_agent '
          'and langchain_google_genai.ChatGoogleGenerativeAI succeeded. '
          'These installed APIs and Node availability are confirmed, not unresolved requirements. '
          'Discover the public interfaces from source; do not replace the installed agent APIs.'
    )