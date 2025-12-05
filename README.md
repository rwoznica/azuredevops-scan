# Azure DevOps Code Analysis Tool

A Python application that scans all repositories across an Azure DevOps organization and generates a comprehensive code analysis report with line counts, language breakdowns, and statistics.

## Features

- Scans all repositories across all projects in an Azure DevOps organization
- Analyzes code using Pygount with support for multiple languages
- Custom parser for Business Central AL files
- Intelligent JSON classification (separates configuration files from data files)
- **AI-powered repository documentation** using Claude 3.5 Sonnet or Gemini 2.0 Flash (optional)
  - Automatically generates comprehensive descriptions for each repository
  - Analyzes code structure, architecture, and technologies
  - Creates individual README files with AI insights
  - Choice between Claude (higher quality) or Gemini (faster, cheaper)
- Generates detailed markdown report with language statistics
- Exports CSV file with per-language breakdowns for data analysis
- Real-time incremental reporting
- Automatic cleanup after each repository scan
- Comprehensive logging to file with configurable levels

## Prerequisites

- Python 3.12 or higher
- Azure DevOps Personal Access Token (PAT) with read access to repositories
- Git installed on your system

## Installation

1. **Clone or download this repository**

2. **Create a Python virtual environment**
   ```bash
   python3 -m venv env
   ```

3. **Activate the virtual environment**
   ```bash
   source env/bin/activate
   ```

4. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

1. **Create a `.env` file** in the project root directory with the following variables:

   ```env
   ORGANIZATION_URL=https://dev.azure.com/your-organization
   PERSONAL_ACCESS_TOKEN=your-pat-token-here
   OUTPUT_FILE=code_analysis_report.md
   CSV_OUTPUT_FILE=code_analysis_report.csv
   LOG_FILE=/tmp/azuredevops_scan.log
   LOG_LEVEL=INFO
   
   # LLM Configuration (Optional - for AI-powered repository descriptions)
   LLM_ENABLED=false
   LLM_PROVIDER=gemini
   LLM_API_KEY=your-api-key
   LLM_MODEL=gemini-2.0-flash-exp
   LLM_PROMPT=Analyze this repository and provide: 1) Purpose and main functionality, 2) Technology stack and programming languages used, 3) Key components and architecture, 4) Main dependencies and integrations, 5) Notable patterns, features, or technical debt. Be concise but comprehensive. Format as markdown with clear sections.
   LLM_OUTPUT_DIR=./repository_descriptions
   LLM_MAX_FILES=50
   LLM_MAX_TOKENS=100000
   ```

2. **Replace the values**:
   - `ORGANIZATION_URL`: Your Azure DevOps organization URL
   - `PERSONAL_ACCESS_TOKEN`: Your Azure DevOps PAT (requires Code > Read permission)
   - `OUTPUT_FILE`: (Optional) Output filename for the markdown report (default: code_analysis_report.md)
   - `CSV_OUTPUT_FILE`: (Optional) Output filename for the CSV report (default: code_analysis_report.csv)
   - `LOG_FILE`: (Optional) Path to log file (default: /tmp/azuredevops_scan.log)
   - `LOG_LEVEL`: (Optional) Logging level - DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)
   - `LLM_ENABLED`: (Optional) Enable AI-powered descriptions - true/false (default: false)
   - `LLM_PROVIDER`: (Optional) AI provider - 'gemini' or 'anthropic' (default: gemini)
   - `LLM_API_KEY`: (Required if LLM_ENABLED=true) 
     - For Gemini: Get from https://aistudio.google.com/apikey
     - For Claude: Get from https://console.anthropic.com/
   - `LLM_MODEL`: (Optional) Model to use
     - Gemini: gemini-2.0-flash-exp (default), gemini-1.5-pro, gemini-1.5-flash
     - Claude: claude-3-5-sonnet-20241022, claude-3-opus-20240229
   - `LLM_PROMPT`: (Optional) Custom prompt for repository analysis
   - `LLM_OUTPUT_DIR`: (Optional) Directory for AI-generated descriptions (default: ./repository_descriptions)
   - `LLM_MAX_FILES`: (Optional) Max files to analyze per repo (default: 50)
   - `LLM_MAX_TOKENS`: (Optional) Max context tokens per analysis (default: 100000)

## Usage

1. **Ensure virtual environment is activated**
   ```bash
   source env/bin/activate
   ```

2. **Run the application**
   ```bash
   python app.py
   ```

3. **View the results**
   
   The application will generate two reports:
   - **Markdown report** (`code_analysis_report.md` by default) containing:
     - Total repository count
     - Lines of code per repository
     - Comment lines
     - Empty lines
     - Programming languages used
   - **CSV report** (`code_analysis_report.csv` by default) containing:
     - One row per Project/Repository/Language combination
     - Columns: Project, Repository, Language, LOC (Code), Comments, Empty Lines
     - Suitable for data analysis and visualization in Excel, Power BI, etc.

## Output Example

The markdown report includes a table with columns:
- Project name
- Repository name
- Lines of Code (LOC)
- Comment lines
- Empty lines
- Languages detected

The CSV report contains one row per Project/Repository/Language combination:
```csv
Project;Repository;Language;LOC (Code);Comments;Empty Lines;AI_Description_Generated
BSN_Central;BSN_Central;JSON (config);15234;0;2341;Yes
BSN_Central;BSN_Central;Markdown;345;123;456;
BSN_Central;Source_Code_Prod;AL;195253;6788;20061;Yes
```

When LLM is enabled, individual repository descriptions are saved to `./repository_descriptions/README-{project}-{repository}.md`

## Notes

- The tool uses shallow clones (`--depth=1`) to optimize performance and disk space
- Temporary directories are cleaned up after each repository scan
- Pseudo-languages (`__binary__`, `__error__`, `__unknown__`, etc.) are filtered from statistics
- AL (Business Central) files are parsed with a custom analyzer
- JSON files are intelligently classified as configuration or data
  - Data JSON files (large arrays, >100KB, containing 'data'/'export'/'dump' in filename) are excluded
  - Configuration JSON files are labeled as "JSON (config)" in reports
- CSV output uses semicolon (`;`) as delimiter for European locale compatibility
- Log files are cleared at the start of each run

### AI-Powered Repository Descriptions

When `LLM_ENABLED=true`:
- **Two provider options:**
  - **Gemini 2.0 Flash** (default): Faster, cheaper ($0.075/$0.30 per 1M tokens), 1M token context, 1,500 RPM
  - **Claude 3.5 Sonnet**: Higher quality prose ($3/$15 per 1M tokens), 200K context, 50 RPM
- Collects up to 50 most important files (README, configs, main code files)
- Generates structured analysis including purpose, tech stack, architecture, and dependencies
- Saves individual markdown files to `./repository_descriptions/`
- Adds "AI_Description_Generated" column to CSV export
- **Cost for 124 repos**: 
  - Gemini: ~$1-2
  - Claude: ~$10-20
- **Time**: 
  - Gemini: ~2-3 seconds per repository
  - Claude: ~3-5 seconds per repository
- **Setup**:
  - Gemini: Get free API key from https://aistudio.google.com/apikey
  - Claude: Get API key from https://console.anthropic.com/
