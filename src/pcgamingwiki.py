import requests
import os
import logging
import re
from pathlib import Path

def fetch_save_locations(game_title, windows_games_dir=""):
    """
    Fetch save game locations from PCGamingWiki by scraping wikitext.
    """
    try:
        # Step 1: Find the page title
        page_title = _find_page_title(game_title)
        if not page_title:
            return []

        # Step 2: Get the wikitext
        wikitext = _get_wikitext(page_title)
        if not wikitext:
            return []

        # Step 3: Parse save locations
        return _parse_save_locations(wikitext, game_title, windows_games_dir)
    except Exception as e:
        logging.error(f"PCGamingWiki error: {e}")
        return []

def _find_page_title(game_title):
    url = "https://www.pcgamingwiki.com/w/api.php"
    
    # Try exact match first
    params = {
        "action": "query",
        "titles": game_title,
        "format": "json"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            pages = data.get("query", {}).get("pages", {})
            for page_id in pages:
                if page_id != "-1":
                    return pages[page_id].get("title")
        
        # Try search if exact match fails
        params = {
            "action": "query",
            "list": "search",
            "srsearch": game_title,
            "format": "json"
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            search_results = data.get("query", {}).get("search", [])
            if search_results:
                return search_results[0].get("title")
    except Exception:
        pass
    return None

def _get_wikitext(page_title):
    url = "https://www.pcgamingwiki.com/w/api.php"
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "wikitext",
        "format": "json"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("parse", {}).get("wikitext", {}).get("*", "")
    except Exception:
        pass
    return None

def _parse_save_locations(wikitext, game_title, windows_games_dir):
    # Regex to find Windows save paths in templates, handling multiple paths per line
    # Pattern: {{Game data/saves|Windows|PATH1 | PATH2}}
    pattern = r'\{\{Game data/saves\|Windows\|([^}]+(?:\|[^}]+)*)\}\}'
    matches = re.findall(pattern, wikitext, re.IGNORECASE)
    
    suggestions = []
    seen_paths = set()
    
    for full_match in matches:
        # A single match might contain multiple paths separated by |
        path_segments = [p.strip() for p in full_match.split('|')]
        
        for raw_path in path_segments:
            # Basic filtering for user-specific or platform-specific skips
            lower_path = raw_path.lower()
            if not raw_path or any(skip in lower_path for skip in ["steam", "linux", "wine", "{{p|uid}}", "{{p|hkcu}}", "{{p|osxhome}}", "{{p|xdgconfighome}}", "{{p|linuxhome}}"]):
                continue
                
            expanded = _expand_wiki_path(raw_path, game_title, windows_games_dir)
            if not expanded:
                continue
            
            # Deduplicate by expanded path
            if expanded.lower() in seen_paths:
                continue
            seen_paths.add(expanded.lower())
            
            # Determine path type for the UI badge
            path_type = _get_path_type(expanded, windows_games_dir)

            suggestions.append({
                "raw_path": raw_path,
                "expanded_path": expanded,
                "path_type": path_type,
                "exists": os.path.exists(expanded)
            })
        
    return suggestions

def _get_path_type(expanded_path, windows_games_dir):
    path_lower = expanded_path.lower()
    
    if "appdata\\roaming" in path_lower:
        return "AppData (Roaming)"
    elif "appdata\\local\\" in path_lower:
        return "AppData (Local)"
    elif "appdata\\locallow" in path_lower:
        return "AppData (LocalLow)"
    elif "documents" in path_lower:
        return "Documents"
    elif "programdata" in path_lower:
        return "ProgramData"
    elif windows_games_dir and windows_games_dir.lower() in path_lower:
        return "Game Folder"
    else:
        return "Other"

def _expand_wiki_path(path, game_title, windows_games_dir):
    # Strip filename or wildcard part (everything after the last backslash or forward slash)
    # The wiki sometimes uses / or \
    clean_path = path
    last_slash = max(clean_path.rfind('\\'), clean_path.rfind('/'))
    if last_slash != -1:
        # Check if the part after the slash looks like a file/wildcard (contains . or *)
        after = clean_path[last_slash+1:]
        if '.' in after or '*' in after:
            clean_path = clean_path[:last_slash+1]

    expanded = clean_path
    
    # PCGamingWiki Template mapping
    substitutions = {
        "{{p|userprofile}}": "%USERPROFILE%",
        "{{p|appdata}}": "%APPDATA%",
        "{{p|localappdata}}": "%LOCALAPPDATA%",
        "{{p|programdata}}": "%PROGRAMDATA%",
        "{{p|public}}": "%PUBLIC%",
        "{{p|programfiles}}": "%PROGRAMFILES%",
        "{{p|programfiles(x86)}}": "%PROGRAMFILES(X86)%",
        "{{p|game}}": os.path.join(windows_games_dir, game_title) if windows_games_dir else ""
    }
    
    for wiki_var, sys_var in substitutions.items():
        if sys_var:
            expanded = expanded.replace(wiki_var, sys_var)
        elif wiki_var in expanded:
            # If we need {{p|game}} but windows_games_dir is missing, we can't expand fully
            return None

    # Replace forward slashes with backslashes for Windows
    expanded = expanded.replace("/", "\\")
    
    # Clean up duplicate backslashes and trailing ones
    expanded = re.sub(r'\\+', r'\\', expanded)
    expanded = expanded.rstrip("\\")
    
    # Final environment expansion
    try:
        final_path = os.path.expandvars(expanded)
        # Ensure we have an absolute path
        return str(Path(final_path).resolve())
    except Exception:
        return None
