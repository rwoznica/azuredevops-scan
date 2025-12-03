# Azure DevOps Code Analysis Tool

A Python application that scans all repositories across an Azure DevOps organization and generates a comprehensive code analysis report with line counts, language breakdowns, and statistics.

## Features

- Scans all repositories across all projects in an Azure DevOps organization
- Analyzes code using Pygount with support for multiple languages
- Custom parser for Business Central AL files
- Generates detailed markdown report with language statistics
- Real-time incremental reporting
- Automatic cleanup after each repository scan

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
   ```

2. **Replace the values**:
   - `ORGANIZATION_URL`: Your Azure DevOps organization URL
   - `PERSONAL_ACCESS_TOKEN`: Your Azure DevOps PAT (requires Code > Read permission)
   - `OUTPUT_FILE`: (Optional) Output filename for the report (default: code_analysis_report.md)

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
   
   The application will generate a markdown report (`code_analysis_report.md` by default) containing:
   - Total repository count
   - Lines of code per repository
   - Comment lines
   - Empty lines
   - Programming languages used

## Output Example

The report includes a table with columns:
- Project name
- Repository name
- Lines of Code (LOC)
- Comment lines
- Empty lines
- Languages detected

## Notes

- The tool uses shallow clones (`--depth=1`) to optimize performance and disk space
- Temporary directories are cleaned up after each repository scan
- Pseudo-languages (`__binary__`, `__error__`, `__unknown__`, etc.) are filtered from statistics
- AL (Business Central) files are parsed with a custom analyzer
