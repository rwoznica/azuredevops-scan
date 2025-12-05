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

# Import LLM library if enabled
if os.getenv('LLM_ENABLED', 'false').lower() == 'true':
    llm_provider = os.getenv('LLM_PROVIDER', 'gemini').lower()
    try:
        if llm_provider == 'anthropic':
            from anthropic import Anthropic
        elif llm_provider == 'gemini':
            import google.generativeai as genai
        else:
            print(f"Warning: Unknown LLM provider '{llm_provider}'. Using 'gemini' as default.")
            import google.generativeai as genai
    except ImportError as e:
        print(f"Warning: LLM library not installed. Install with: pip install {llm_provider if llm_provider == 'anthropic' else 'google-generativeai'}")
        os.environ['LLM_ENABLED'] = 'false'

# --- CONFIGURATION ---
ORGANIZATION_URL = os.getenv('ORGANIZATION_URL')
PERSONAL_ACCESS_TOKEN = os.getenv('PERSONAL_ACCESS_TOKEN')
OUTPUT_FILE = os.getenv('OUTPUT_FILE', 'code_analysis_report.md')
CSV_OUTPUT_FILE = os.getenv('CSV_OUTPUT_FILE', 'code_analysis_report.csv')
LOG_FILE = os.getenv('LOG_FILE', '/tmp/azuredevops_scan.log')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

# LLM Configuration
LLM_ENABLED = os.getenv('LLM_ENABLED', 'false').lower() == 'true'
LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'gemini').lower()  # 'gemini' or 'anthropic'
LLM_API_KEY = os.getenv('LLM_API_KEY', '')
LLM_MODEL = os.getenv('LLM_MODEL', 'gemini-2.0-flash-exp')
LLM_PROMPT = os.getenv('LLM_PROMPT', 'Analyze this repository.')
LLM_OUTPUT_DIR = os.getenv('LLM_OUTPUT_DIR', './repository_descriptions')
LLM_MAX_FILES = int(os.getenv('LLM_MAX_FILES', '50'))
LLM_MAX_TOKENS = int(os.getenv('LLM_MAX_TOKENS', '100000'))

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

def collect_repository_context(repo_dir, max_files=50, max_tokens=100000):
    """Collect important files from repository for LLM analysis."""
    important_files = []
    
    # Priority file patterns
    priority_patterns = [
        'readme', 'readme.md', 'readme.txt',
        'package.json', 'requirements.txt', '*.csproj', 'pom.xml', 'build.gradle',
        'app.json', 'manifest.json', 'appsettings.json',
        'main.', 'index.', 'app.', 'program.cs', 'startup.cs',
        'dockerfile', 'docker-compose', '.gitignore',
        'changelog', 'license', 'contributing'
    ]
    
    # Collect files with priority scoring
    files_with_score = []
    
    for root, dirs, files in os.walk(repo_dir):
        # Skip ignored directories
        dirs[:] = [d for d in dirs if not any(pattern in d.lower() for pattern in IGNORE_PATTERNS)]
        
        for file in files:
            file_path = os.path.join(root, file)
            file_lower = file.lower()
            
            # Skip ignored patterns
            if any(pattern in file_path for pattern in IGNORE_PATTERNS):
                continue
            
            # Calculate priority score
            score = 0
            for pattern in priority_patterns:
                if pattern in file_lower:
                    score += 10
                    break
            
            # Boost score for certain extensions
            if any(file_lower.endswith(ext) for ext in ['.md', '.json', '.txt', '.yml', '.yaml']):
                score += 5
            if any(file_lower.endswith(ext) for ext in ['.cs', '.py', '.js', '.ts', '.al']):
                score += 3
            
            # Penalize large files
            try:
                size = os.path.getsize(file_path)
                if size > 100000:  # >100KB
                    score -= 5
            except:
                pass
            
            files_with_score.append((file_path, score, file))
    
    # Sort by score and take top files
    files_with_score.sort(key=lambda x: x[1], reverse=True)
    selected_files = files_with_score[:max_files]
    
    # Build context string
    context_parts = []
    total_chars = 0
    max_chars = max_tokens * 3  # Rough estimate: 1 token ≈ 3-4 chars
    
    # Add directory structure first
    context_parts.append("# Repository Structure\n")
    context_parts.append(get_directory_tree(repo_dir, max_depth=3))
    context_parts.append("\n\n")
    
    # Add file contents
    for file_path, score, filename in selected_files:
        if total_chars >= max_chars:
            break
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(50000)  # Max 50KB per file
                relative_path = os.path.relpath(file_path, repo_dir)
                
                file_section = f"\n## File: {relative_path}\n```\n{content}\n```\n\n"
                
                if total_chars + len(file_section) < max_chars:
                    context_parts.append(file_section)
                    total_chars += len(file_section)
        except Exception as e:
            logger.debug(f"Error reading file {file_path}: {e}")
    
    return ''.join(context_parts)

def get_directory_tree(directory, max_depth=3, current_depth=0, prefix=""):
    """Generate a simple directory tree string."""
    if current_depth >= max_depth:
        return ""
    
    tree = []
    try:
        items = sorted(os.listdir(directory))
        for item in items[:20]:  # Limit items per level
            if any(pattern in item for pattern in IGNORE_PATTERNS):
                continue
            
            item_path = os.path.join(directory, item)
            if os.path.isdir(item_path):
                tree.append(f"{prefix}├── {item}/")
                if current_depth < max_depth - 1:
                    subtree = get_directory_tree(item_path, max_depth, current_depth + 1, prefix + "│   ")
                    if subtree:
                        tree.append(subtree)
            else:
                tree.append(f"{prefix}├── {item}")
    except Exception as e:
        logger.debug(f"Error reading directory {directory}: {e}")
    
    return "\n".join(tree)

def analyze_repository_with_llm(repo_dir, repo_name, project_name):
    """Analyze repository using LLM (Gemini or Claude) and generate description."""
    if not LLM_ENABLED or not LLM_API_KEY:
        return False
    
    try:
        logger.info(f"  Generating AI description for {repo_name}...")
        
        # Collect repository context
        context = collect_repository_context(repo_dir, LLM_MAX_FILES, LLM_MAX_TOKENS)
        
        description = None
        
        if LLM_PROVIDER == 'anthropic':
            # Use Claude
            client = Anthropic(api_key=LLM_API_KEY)
            message = client.messages.create(
                model=LLM_MODEL,
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": f"{LLM_PROMPT}\n\nRepository: {project_name}/{repo_name}\n\n{context}"
                }]
            )
            description = message.content[0].text
            
        elif LLM_PROVIDER == 'gemini':
            # Use Gemini with retry logic for rate limits
            import time
            genai.configure(api_key=LLM_API_KEY)
            model = genai.GenerativeModel(LLM_MODEL)
            
            prompt = f"{LLM_PROMPT}\n\nRepository: {project_name}/{repo_name}\n\n{context}"
            
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = model.generate_content(prompt)
                    description = response.text
                    break
                except Exception as e:
                    if '429' in str(e) and 'Quota exceeded' in str(e):
                        if attempt < max_retries - 1:
                            # Extract retry delay from error message if available
                            import re
                            retry_match = re.search(r'retry in ([\d.]+)s', str(e))
                            if retry_match:
                                wait_time = float(retry_match.group(1)) + 2  # Add 2 seconds buffer
                            else:
                                wait_time = 60  # Default wait time
                            logger.info(f"  Rate limit hit, waiting {wait_time:.0f} seconds...")
                            time.sleep(wait_time)
                        else:
                            raise
                    else:
                        raise
        
        else:
            logger.error(f"  Unknown LLM provider: {LLM_PROVIDER}")
            return False
        
        if not description:
            logger.warning(f"  Empty response from LLM for {repo_name}")
            return False
        
        # Ensure output directory exists
        os.makedirs(LLM_OUTPUT_DIR, exist_ok=True)
        
        # Save to file
        output_file = os.path.join(LLM_OUTPUT_DIR, f"README-{project_name}-{repo_name}.md")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"# {project_name} / {repo_name}\n\n")
            f.write(f"*AI-Generated Repository Analysis*\n\n")
            f.write(f"---\n\n")
            f.write(description)
            f.write(f"\n\n---\n\n")
            f.write(f"*Generated on: {os.popen('date').read().strip()}*\n")
            f.write(f"*Model: {LLM_MODEL} ({LLM_PROVIDER})*\n")
        
        logger.info(f"  ✓ AI description saved to {output_file}")
        
        # Add delay between requests for Gemini free tier (250K tokens/minute limit)
        if LLM_PROVIDER == 'gemini':
            import time
            time.sleep(15)  # Wait 15 seconds between requests to stay under rate limit
        
        return True
        
    except Exception as e:
        logger.warning(f"  LLM analysis failed for {repo_name}: {e}")
        return False

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
        csv_headers = ["Project", "Repository", "Language", "LOC (Code)", "Comments", "Empty Lines"]
        if LLM_ENABLED:
            csv_headers.append("AI_Description_Generated")
        
        with open(CSV_OUTPUT_FILE, "w", encoding="utf-8", newline='') as csvfile:
            csv_writer = csv.writer(csvfile, delimiter=';')
            csv_writer.writerow(csv_headers)
        
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
                    
                    # LLM Analysis (if enabled)
                    llm_success = False
                    if LLM_ENABLED:
                        llm_success = analyze_repository_with_llm(target_dir, repo_name, project_name)
                    
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
                            for i, lang_data in enumerate(stats['language_breakdown']):
                                row = [
                                    project_name,
                                    repo_name,
                                    lang_data['language'],
                                    lang_data['code'],
                                    lang_data['documentation'],
                                    lang_data['empty']
                                ]
                                # Add LLM status only to first row per repository
                                if LLM_ENABLED and i == 0:
                                    row.append('Yes' if llm_success else 'No')
                                elif LLM_ENABLED:
                                    row.append('')
                                csv_writer.writerow(row)
                        else:
                            # If no languages detected, write a single row with empty language
                            row = [project_name, repo_name, "", 0, 0, 0]
                            if LLM_ENABLED:
                                row.append('Yes' if llm_success else 'No')
                            csv_writer.writerow(row)
                    
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