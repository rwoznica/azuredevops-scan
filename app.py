import os
import shutil
import tempfile
import logging
import base64
from urllib.parse import urlparse, urlunparse, quote
from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication
from azure.devops.v7_0.git.models import GitRepository
from pygount import ProjectSummary, SourceAnalysis
from tabulate import tabulate
from git import Repo
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- CONFIGURATION ---
ORGANIZATION_URL = os.getenv('ORGANIZATION_URL')
PERSONAL_ACCESS_TOKEN = os.getenv('PERSONAL_ACCESS_TOKEN')
OUTPUT_FILE = os.getenv('OUTPUT_FILE', 'code_analysis_report.md')
# extensions to ignore to speed up processing
IGNORE_PATTERNS = [".git", "node_modules", "bin", "obj", ".vs"] 

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_all_repositories(connection):
    """Retrieves all repositories across all projects in the Org."""
    git_client = connection.clients.get_git_client()
    core_client = connection.clients.get_core_client()
    
    all_repos = []
    projects = core_client.get_projects()
    
    logging.info(f"Found {len(projects)} projects. Scanning for repositories...")
    
    for project in projects:
        repos = git_client.get_repositories(project.id)
        for repo in repos:
            all_repos.append((project.name, repo))
            
    return all_repos

def get_repository_branches(connection, project_name, repo_id):
    """Retrieves all branches for a specific repository."""
    git_client = connection.clients.get_git_client()
    try:
        branches = git_client.get_branches(repo_id, project_name)
        return [branch.name for branch in branches]
    except Exception as e:
        logging.warning(f"Failed to get branches for {repo_id}: {e}")
        return []

def analyze_directory(directory):
    """Uses Pygount to count lines in a directory."""
    try:
        # pygount searches files and counts based on extensions
        summary = ProjectSummary()
        
        # Walk through directory manually
        import os
        for root, dirs, files in os.walk(directory):
            # Skip ignored directories
            dirs[:] = [d for d in dirs if not any(pattern in d for pattern in IGNORE_PATTERNS)]
            
            for file in files:
                file_path = os.path.join(root, file)
                # Skip files in ignored patterns
                if any(pattern in file_path for pattern in IGNORE_PATTERNS):
                    continue
                
                # Check if this is an AL file (Business Central)
                if file_path.lower().endswith('.al'):
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()
                            code_lines = 0
                            comment_lines = 0
                            empty_lines = 0
                            in_block_comment = False
                            
                            for line in lines:
                                stripped = line.strip()
                                if not stripped:
                                    empty_lines += 1
                                elif in_block_comment:
                                    comment_lines += 1
                                    if '*/' in stripped:
                                        in_block_comment = False
                                elif stripped.startswith('//'):
                                    comment_lines += 1
                                elif stripped.startswith('/*'):
                                    comment_lines += 1
                                    if '*/' not in stripped:
                                        in_block_comment = True
                                else:
                                    code_lines += 1
                            
                            # Create a manual analysis entry for AL
                            from pygount.analysis import SourceAnalysis as SA, SourceState
                            manual_analysis = SA(
                                path=file_path,
                                language='AL',
                                group='code',
                                code=code_lines,
                                documentation=comment_lines,
                                empty=empty_lines,
                                string=0,
                                state=SourceState.analyzed
                            )
                            summary.add(manual_analysis)
                    except Exception as e:
                        logging.debug(f"Error processing AL file {file_path}: {e}")
                    continue
                
                try:
                    # pygount will try to infer the language
                    analysis = SourceAnalysis.from_file(file_path, "pygount", fallback_encoding="utf-8")
                    
                    # Skip pseudo-languages
                    if analysis.language not in ['__binary__', '__error__', '__unknown__', '__empty__', '__generated__']:
                        summary.add(analysis)
                except Exception as e:
                    # Suppress warnings for unknown languages
                    if "unknown language" not in str(e).lower():
                        logging.debug(f"Skipping {file_path}: {e}")
                
        # Filter out pseudo-languages from the language list
        filtered_languages = {
            lang: data for lang, data in summary.language_to_language_summary_map.items()
            if lang not in ['__binary__', '__error__', '__unknown__', '__empty__', '__generated__']
        }
        
        return {
            "code": summary.total_code_count,
            "documentation": summary.total_documentation_count, # comments/docstrings
            "empty": summary.total_empty_count,
            "languages": ", ".join(sorted(filtered_languages.keys())) if filtered_languages else ""
        }
    except Exception as e:
        logging.error(f"Error analyzing {directory}: {e}")
        return {"code": 0, "documentation": 0, "empty": 0, "languages": ""}

def main():
    # Connect to ADO
    credentials = BasicAuthentication('', PERSONAL_ACCESS_TOKEN)
    connection = Connection(base_url=ORGANIZATION_URL, creds=credentials)
    
    repos = get_all_repositories(connection)
    logging.info(f"Total repositories found: {len(repos)}")

    results = []
    
    # Initialize the report file with headers
    headers = ["Project", "Repository / Branch", "LOC (Code)", "Comments", "Empty Lines", "Languages"]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(f"""
# Azure DevOps Code Analysis Report

**Organization:** {ORGANIZATION_URL}
**Total Repositories:** {len(repos)}
**Status:** In Progress...

## Detailed Breakdown

""")
        # Write table headers
        f.write("| " + " | ".join(headers) + " |\n")
        f.write("|" + "|".join(["-" * (len(h) + 2) for h in headers]) + "|\n")
        f.flush()

    # Create a temporary directory for cloning
    with tempfile.TemporaryDirectory() as temp_dir:
        for project_name, repo in repos:
            repo_name = repo.name
            repo_id = repo.id
            remote_url = repo.remote_url
            
            logging.info(f"Processing: {project_name} / {repo_name}")
            
            # Get all branches for this repository
            branches = get_repository_branches(connection, project_name, repo_id)
            logging.info(f"Found {len(branches)} branches in {repo_name}")
            
            if not branches:
                logging.warning(f"No branches found for {repo_name}, skipping...")
                with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                    f.write(f"| {project_name} | {repo_name} | No branches | - | - | - |\n")
                    f.flush()
                continue
            
            # Process each branch
            for branch_name in branches:
                target_dir = os.path.join(temp_dir, project_name, repo_name, branch_name.replace('/', '_'))
                
                logging.info(f"  Analyzing branch: {branch_name}")
                
                try:
                    # Clone with specific branch
                    import subprocess
                    
                    # Azure DevOps authentication: Base64 encode ':PAT'
                    auth_bytes = f':{PERSONAL_ACCESS_TOKEN}'.encode('utf-8')
                    base64_auth = base64.b64encode(auth_bytes).decode('utf-8')
                    
                    # Create target directory
                    os.makedirs(target_dir, exist_ok=True)
                    
                    # Run git clone with authentication header and specific branch
                    result = subprocess.run(
                        ['git', 'clone', '--depth=1', '--branch', branch_name, '-v',
                         '-c', f'http.extraHeader=Authorization: Basic {base64_auth}',
                         remote_url, target_dir],
                        capture_output=True,
                        text=True
                    )
                    
                    if result.returncode != 0:
                        raise Exception(f"Git clone failed: {result.stderr}")
                    
                    # Analyze
                    stats = analyze_directory(target_dir)
                    
                    result_row = [
                        project_name,
                        f"{repo_name} / {branch_name}",
                        stats['code'],
                        stats['documentation'],
                        stats['empty'],
                        stats['languages']
                    ]
                    results.append(result_row)
                    
                    # Immediately write this result to the report with indentation for branches
                    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                        # Main repo row (first branch) or branch row (indented)
                        display_name = f"**{repo_name}**" if branch_name == branches[0] else f"&nbsp;&nbsp;&nbsp;&nbsp;↳ *{branch_name}*"
                        f.write(f"| {project_name} | {display_name} | {stats['code']:,} | {stats['documentation']:,} | {stats['empty']:,} | {stats['languages']} |\n")
                        f.flush()
                    
                    # Clean up the cloned repository to save disk space
                    try:
                        shutil.rmtree(target_dir)
                        logging.debug(f"Cleaned up {target_dir}")
                    except Exception as cleanup_error:
                        logging.warning(f"Failed to cleanup {target_dir}: {cleanup_error}")
                    
                except Exception as e:
                    logging.error(f"Failed to clone or process {repo_name} branch {branch_name}: {e}")
                    
                    # Write error result to the report
                    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                        display_name = f"**{repo_name}**" if branch_name == branches[0] else f"&nbsp;&nbsp;&nbsp;&nbsp;↳ *{branch_name}*"
                        f.write(f"| {project_name} | {display_name} | ERROR | 0 | 0 | - |\n")
                        f.flush()
                    
                    # Try to clean up even on error
                    try:
                        if os.path.exists(target_dir):
                            shutil.rmtree(target_dir)
                    except Exception:
                        pass

    # --- Update Report with Final Summary ---
    # Calculate Totals
    total_code = sum(r[2] for r in results if isinstance(r[2], int))
    total_comments = sum(r[3] for r in results if isinstance(r[3], int))
    
    # Read the existing report
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        report_content = f.read()
    
    # Update the status and totals
    report_content = report_content.replace(
        "**Status:** In Progress...",
        f"**Status:** Complete\n**Total Lines of Code:** {total_code:,}\n**Total Lines of Comments:** {total_comments:,}"
    )
    
    # Write the final report
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)
    
    logging.info(f"Analysis complete. Report saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()