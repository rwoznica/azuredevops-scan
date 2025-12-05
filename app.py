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
CSV_OUTPUT_FILE = os.getenv('CSV_OUTPUT_FILE', 'code_analysis_report.csv')
LOG_FILE = os.getenv('LOG_FILE', '/tmp/azuredevops_scan.log')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
# extensions to ignore to speed up processing
IGNORE_PATTERNS = [".git", "node_modules", "bin", "obj", ".vs"] 

# Setup Logging
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOG_LEVEL))

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# File handler - use 'w' mode to clear the file on each run
file_handler = logging.FileHandler(LOG_FILE, mode='w')
file_handler.setLevel(getattr(logging, LOG_LEVEL))
file_handler.setFormatter(formatter)

# Add handlers to logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

logger.info(f"Logging initialized. Log file: {LOG_FILE}")

def get_all_repositories(connection):
    """Retrieves all repositories across all projects in the Org."""
    git_client = connection.clients.get_git_client()
    core_client = connection.clients.get_core_client()
    
    all_repos = []
    projects = core_client.get_projects()
    
    logger.info(f"Found {len(projects)} projects. Scanning for repositories...")
    
    for project in projects:
        repos = git_client.get_repositories(project.id)
        for repo in repos:
            all_repos.append((project.name, repo))
            
    return all_repos

def analyze_directory(directory):
    """Uses Pygount to count lines in a directory."""
    try:
        # pygount searches files and counts based on extensions
        summary = ProjectSummary()
        
        # Walk through directory manually
        import os
        import json
        for root, dirs, files in os.walk(directory):
            # Skip ignored directories
            dirs[:] = [d for d in dirs if not any(pattern in d for pattern in IGNORE_PATTERNS)]
            
            for file in files:
                file_path = os.path.join(root, file)
                # Skip files in ignored patterns
                if any(pattern in file_path for pattern in IGNORE_PATTERNS):
                    continue
                
                # Check if this is a JSON file - distinguish config vs data
                if file_path.lower().endswith('.json'):
                    try:
                        # Heuristics to determine if JSON is configuration or data:
                        # Config files are typically: small, have mixed types, contain settings/metadata keys
                        # Data files are typically: large, array-heavy, repetitive structure
                        
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                            
                        # Check file size (data files are usually larger)
                        file_size = len(content)
                        
                        # Try to parse JSON
                        try:
                            json_obj = json.loads(content)
                            
                            # Heuristics for data files:
                            # 1. Root is a large array (common in data exports)
                            # 2. Large file size (>100KB typically data)
                            # 3. Highly repetitive structure (many identical keys)
                            
                            is_data = False
                            
                            # Check if root is a large array
                            if isinstance(json_obj, list) and len(json_obj) > 20:
                                is_data = True
                            
                            # Check file size
                            elif file_size > 102400:  # 100KB
                                is_data = True
                            
                            # Check for data-like filenames
                            filename_lower = os.path.basename(file_path).lower()
                            if any(pattern in filename_lower for pattern in ['data', 'export', 'dump', 'records', 'rows', 'backup']):
                                is_data = True
                            
                            # Config file indicators (override data classification if found)
                            config_indicators = ['package.json', 'tsconfig', 'jsconfig', 'settings', 
                                               'config', 'launch', 'tasks', 'manifest', 'schema',
                                               '.eslintrc', '.prettierrc', 'appsettings', 'web.config']
                            if any(indicator in filename_lower for indicator in config_indicators):
                                is_data = False
                            
                            # If classified as data, skip counting it
                            if is_data:
                                logger.debug(f"Skipping data JSON: {file_path}")
                                continue
                                
                        except json.JSONDecodeError:
                            # If we can't parse it, treat it as config (safer to include)
                            pass
                    except Exception as e:
                        logger.debug(f"Error analyzing JSON file {file_path}: {e}")
                    
                    # Process as regular JSON config file
                    try:
                        analysis = SourceAnalysis.from_file(file_path, "pygount", fallback_encoding="utf-8")
                        if analysis.language not in ['__binary__', '__error__', '__unknown__', '__empty__', '__generated__']:
                            # Create a custom analysis with renamed language
                            from pygount.analysis import SourceAnalysis as SA, SourceState
                            config_analysis = SA(
                                path=file_path,
                                language='JSON (config)',
                                group='code',
                                code=analysis.code_count,
                                documentation=analysis.documentation_count,
                                empty=analysis.empty_count,
                                string=analysis.string_count,
                                state=SourceState.analyzed
                            )
                            summary.add(config_analysis)
                    except Exception as e:
                        logger.debug(f"Error processing JSON config {file_path}: {e}")
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
                        logger.debug(f"Error processing AL file {file_path}: {e}")
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
                        logger.debug(f"Skipping {file_path}: {e}")
                
        # Filter out pseudo-languages from the language list
        filtered_languages = {
            lang: data for lang, data in summary.language_to_language_summary_map.items()
            if lang not in ['__binary__', '__error__', '__unknown__', '__empty__', '__generated__']
        }
        
        # Build language breakdown for CSV export
        language_breakdown = []
        for lang, lang_summary in sorted(filtered_languages.items()):
            language_breakdown.append({
                "language": lang,
                "code": lang_summary.code_count,
                "documentation": lang_summary.documentation_count,
                "empty": lang_summary.empty_count
            })
        
        return {
            "code": summary.total_code_count,
            "documentation": summary.total_documentation_count, # comments/docstrings
            "empty": summary.total_empty_count,
            "languages": ", ".join(sorted(filtered_languages.keys())) if filtered_languages else "",
            "language_breakdown": language_breakdown
        }
    except Exception as e:
        logger.error(f"Error analyzing {directory}: {e}", exc_info=True)
        return {"code": 0, "documentation": 0, "empty": 0, "languages": "", "language_breakdown": []}

def main():
    try:
        # Connect to ADO
        credentials = BasicAuthentication('', PERSONAL_ACCESS_TOKEN)
        connection = Connection(base_url=ORGANIZATION_URL, creds=credentials)
        
        repos = get_all_repositories(connection)
        logger.info(f"Total repositories found: {len(repos)}")

        results = []
        csv_rows = []
        
        # Initialize the CSV file with headers
        import csv
        with open(CSV_OUTPUT_FILE, "w", encoding="utf-8", newline='') as csvfile:
            csv_writer = csv.writer(csvfile, delimiter=';')
            csv_writer.writerow(["Project", "Repository", "Language", "LOC (Code)", "Comments", "Empty Lines"])
        
        logger.info(f"CSV output will be written to: {CSV_OUTPUT_FILE}")
        
        # Initialize the report file with headers
        headers = ["Project", "Repository", "LOC (Code)", "Comments", "Empty Lines", "Languages"]
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
                remote_url = repo.remote_url
                
                target_dir = os.path.join(temp_dir, project_name, repo_name)
                
                logger.info(f"Processing: {project_name} / {repo_name}")
                
                try:
                    # Shallow clone (depth=1) is much faster and uses less storage
                    # Use subprocess for better control over git authentication
                    import subprocess
                    
                    # Azure DevOps authentication: Base64 encode ':PAT'
                    auth_bytes = f':{PERSONAL_ACCESS_TOKEN}'.encode('utf-8')
                    base64_auth = base64.b64encode(auth_bytes).decode('utf-8')
                    
                    # Create target directory
                    os.makedirs(target_dir, exist_ok=True)
                    
                    # Run git clone with authentication header
                    result = subprocess.run(
                        ['git', 'clone', '--depth=1', '-v',
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
                        repo_name,
                        stats['code'],
                        stats['documentation'],
                        stats['empty'],
                        stats['languages']
                    ]
                    results.append(result_row)
                    
                    # Write language breakdown to CSV
                    with open(CSV_OUTPUT_FILE, "a", encoding="utf-8", newline='') as csvfile:
                        csv_writer = csv.writer(csvfile, delimiter=';')
                        if stats['language_breakdown']:
                            for lang_data in stats['language_breakdown']:
                                csv_writer.writerow([
                                    project_name,
                                    repo_name,
                                    lang_data['language'],
                                    lang_data['code'],
                                    lang_data['documentation'],
                                    lang_data['empty']
                                ])
                        else:
                            # If no languages detected, write a single row with empty language
                            csv_writer.writerow([project_name, repo_name, "", 0, 0, 0])
                    
                    # Immediately write this result to the report
                    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                        f.write(f"| {project_name} | {repo_name} | {stats['code']:,} | {stats['documentation']:,} | {stats['empty']:,} | {stats['languages']} |\n")
                        f.flush()
                    
                    # Clean up the cloned repository to save disk space
                    try:
                        shutil.rmtree(target_dir)
                        logger.debug(f"Cleaned up {target_dir}")
                    except Exception as cleanup_error:
                        logger.warning(f"Failed to cleanup {target_dir}: {cleanup_error}")
                    
                except Exception as e:
                    logger.error(f"Failed to clone or process {repo_name}: {e}", exc_info=True)
                    result_row = [project_name, repo_name, "ERROR", 0, 0, 0]
                    results.append(result_row)
                    
                    # Write error to CSV
                    with open(CSV_OUTPUT_FILE, "a", encoding="utf-8", newline='') as csvfile:
                        csv_writer = csv.writer(csvfile, delimiter=';')
                        csv_writer.writerow([project_name, repo_name, "ERROR", 0, 0, 0])
                    
                    # Write error result to the report
                    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                        f.write(f"| {project_name} | {repo_name} | ERROR | 0 | 0 | - |\n")
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
        
        logger.info(f"Analysis complete. Report saved to {OUTPUT_FILE}")
        logger.info(f"CSV report saved to {CSV_OUTPUT_FILE}")
        logger.info(f"Log file saved to {LOG_FILE}")
    
    except Exception as e:
        logger.critical(f"Fatal error in main execution: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()