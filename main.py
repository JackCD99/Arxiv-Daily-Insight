import urllib.request
import urllib.parse
import urllib.error
import feedparser
import json
import requests
import markdown
import time
import re
import datetime
from time import mktime
import os
import yaml  # Used for parsing the external config file
import glob

# =========================================================================================
#      █████╗ ██████╗ ██╗  ██╗██╗██╗   ██╗     ██████╗ ██████╗ ███████╗████████╗███████╗
#     ██╔══██╗██╔══██╗╚██╗██╔╝██║██║   ██║     ██╔══██╗██╔══██╗██╔════╝╚══██╔══╝██╔════╝
#     ███████║██████╔╝ ╚███╔╝ ██║██║   ██║     ██████╔╝██║  ██║█████╗     ██║   █████╗  
#     ██╔══██║██╔══██╗ ██╔██╗ ██║╚██╗ ██╔╝     ██╔═══╝ ██║  ██║██╔══╝     ██║   ██╔══╝  
#     ██║  ██║██║  ██║██╔╝ ██╗██║ ╚████╔╝      ██║     ██████╔╝███████╗   ██║   ███████╗
#     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚════╝      ╚═╝     ╚═════╝ ╚══════╝   ╚═╝   ╚══════╝
# =========================================================================================
#
#                    [ THE ENHANCED MULTI-STAGE TELEMETRY FUNNEL ]
#
#   [ arXiv API ] 
#         |  Fetch 24h Window (Rolling Anchor)
#         V  (Count: X)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 1: PERSISTENT CACHE & HARD HEURISTIC (fetch_and_local_filter_arxiv)         |
#   |   1. Cache Memory  : Load LATEST Daily Ledger [Success]_Evaluations_YYYY-MM-DD    |
#   |   2. Score Filter  : Auto-skip papers with historical Relevance < 4.              |
#   |   3. Hard Rules    : cs.* Subcategories, Keywords, Page Count (>= 5 pages).       |
#   +-----+-----------------------------------------------------------------------------+
#         |
#         V  (Count: Y | Survival Rate: Y/X%)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 2: SEMANTIC NOISE ELIMINATION (pre_filter_by_titles)                        |
#   |   * LLM Agent: deepseek-chat (Speed Optimized | Token Tracking On)                |
#   |   1. Elimination   : Binary logic (Discard only unambiguous noise).               |
#   |   2. Default State : Default to KEEP for ambiguous/potential inspiration.         |
#   |   3. Logging       : Record Stage-2 Drops as Relevance: -1 in Ledger.             |
#   +-----+-----------------------------------------------------------------------------+
#         |
#         V  (Count: * | Token Usage: T1)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 3: INSPIRATION-CENTRIC SCORING (evaluate_and_filter_papers)                 |
#   |   * LLM Agent: deepseek-reasoner (CoT Logic | Heavy A-Weighting)                  |
#   |   1. Matrix        : A (Alignment/Transfer) + D (Domain) + M (Method).            |
#   |   2. Veto Gate     : D=0 or A=-1 triggers instant Relevance: 0.                   |
#   |   3. Refinement    : THE CROWN RULE (Perfect Match) vs. TRANSFER SYNERGY.         |
#   +-----+-----------------------------------------------------------------------------+
#         |
#         V  (Full Scored Objects | Token Usage: T2)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 4: DAILY LEDGER PERSISTENCE & RANKING (run_job)                             |
#   |   1. Ledger Appending: Atomic 'a' mode write to [Success]_Evaluations_Today.txt   |
#   |   2. Primary Sort    : Relevance Score (0-5) DESC.                                |
#   |   3. Tie-Breaker     : Bonus Score (Venue Weight + Oral + Tracked Author).        |
#   |   4. Truncation      : Isolate Top-K (Z) and Runner-ups (Score >= 3).             |
#   +-----+-----------------------------------------------------------------------------+
#         |
#         V  (Final Selection: Z | Token Usage: T-Total)
#   +-----------------------------------------------------------------------------------+
#   | STAGE 5: UI SYNTHESIS & STATS DELIVERY (beautify_to_markdown & PushPlus)          |
#   |   1. Beautification : Pro-Academic Layout, Inline Sub-boxes, Strategic Bolding.   |
#   |   2. Telemetry Card : Render Stats (X, Y, *, Z), Token Consumption, Duration.     |
#   |   3. Runner-ups     : Generate Chinese Collapsible <details> for High-Score Drops.|
#   +-----------------------------------------------------------------------------------+
# =========================================================================================

# =====================================================================
# 1. Configuration Loading & Environment Setup
# =====================================================================
try:
    # Dynamically load configuration from YAML to decouple parameters from core logic
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"❌ [FATAL] Failed to read config.yaml. Please verify file integrity. Details: {e}")
    exit(1)

# Dynamically inject proxy settings into the OS environment if specified in config
if config.get('proxy', {}).get('http'):
    os.environ["http_proxy"] = config['proxy']['http']
if config.get('proxy', {}).get('https'):
    os.environ["https_proxy"] = config['proxy']['https']


# =====================================================================
# 2. Utility & IO Functions
# =====================================================================

def normalize_title(title):
    """
    Sanitizes paper titles to ensure consistent cache matching.
    Removes newline characters, normalizes multiple spaces, and converts to lowercase.
    """
    return re.sub(r'\s+', ' ', title).strip().lower()


def call_deepseek(messages, model_name, temperature, expect_json=False):
    """
    Enhanced API caller to track token usage for telemetry.
    Returns: (content, prompt_tokens, completion_tokens)
    """
    url = config['llm']['base_url']
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['auth']['llm_api_key']}" 
    }
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
    }
    if expect_json and "reasoner" not in model_name.lower():
        payload["response_format"] = {"type": "json_object"}

    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status() 
    res_data = response.json()
    
    # Extract telemetry data from the provider's response
    content = res_data['choices'][0]['message']['content']
    usage = res_data.get('usage', {})
    p_tokens = usage.get('prompt_tokens', 0)
    c_tokens = usage.get('completion_tokens', 0)
    
    return content, p_tokens, c_tokens


def extract_json_from_text(text):
    """
    Regex-based fallback extraction to isolate JSON structures from LLM outputs
    that hallucinate conversational padding (e.g., "Here is the JSON you requested: {...}").
    """
    match_obj = re.search(r'\{[\s\S]*\}', text)
    if match_obj:
        return match_obj.group(0)
    
    match_arr = re.search(r'\[[\s\S]*\]', text)
    if match_arr:
        return match_arr.group(0)
        
    return text


def get_score_distribution_url(all_evaluations):
    """
    Generates an Apple-style minimalist smooth line chart with shadow/area fill.
    Visualizes the quality distribution of ALL processed papers today.
    """
    from collections import Counter
    import urllib.parse

    # 1. Statistical analysis of ALL papers (including -1 as score 0 for the curve)
    # Mapping: -1 (Rejected) -> 0, and normal scores 0-5.
    raw_scores = []
    for r in all_evaluations:
        s = r.get("relevance", -1)
        # 将 -1 归类为 0 分（底噪），让曲线从最左侧开始
        raw_scores.append(s if s >= 0 else 0)
    
    if not raw_scores:
        return ""

    counts = Counter(raw_scores)
    # X-axis: 0 (Irrelevant/Rejected) to 5 (Core Match)
    x_labels = ["Noise", "1", "2", "3", "4", "Match"]
    y_values = [counts.get(i, 0) for i in range(6)]

    max_val = max(y_values) if y_values else 10
    suggested_max = int(max_val * 1.15) + 1

    # 2. Apple Style Chart Configuration
    # Minimalist, no grid, smooth curve, monotonic cubic interpolation
    chart_config = {
        "type": "line",
        "data": {
            "labels": ["Noise", "1分", "2分", "3分", "4分", "Match"],
            "datasets": [{
                "data": y_values,
                "fill": True,
                "backgroundColor": "rgba(52, 152, 219, 0.12)",
                "borderColor": "#3498db",
                "borderWidth": 3,
                "pointRadius": 0,
                "lineTension": 0.4
            }]
        },
        "options": {
            "legend": {"display": False},
            "scales": {
                "xAxes": [{
                    "gridLines": {"display": False},
                    "ticks": {"fontColor": "#afafb6", "fontSize": 10}
                }],
                "yAxes": [{
                    "display": False,
                    "gridLines": {"display": False},
                    "ticks": {
                        "beginAtZero": True,
                        "suggestedMax": suggested_max
                    }
                }]
            },
            "plugins": { "datalabels": {"display": False} }
        }
    }

    config_str = json.dumps(chart_config)
    encoded_config = urllib.parse.quote(config_str)
    # High resolution for mobile retina screens
    return f"https://quickchart.io/chart?c={encoded_config}&width=600&height=200&bkg=white"


def get_historical_skip_titles():
    """
    Loads a comprehensive ledger of all previously processed papers to prevent redundant API calls.
    Iterates through all historical logs via `os.listdir` to avoid `glob` syntax edge cases.
    
    Returns:
        set: A unique collection of normalized paper titles.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "Logs")
    skip_titles = set()
    
    if not os.path.exists(log_dir):
        print(f"⚠️  [WARN] Log directory missing at {log_dir}. Proceeding with empty cache.")
        return skip_titles
    
    # Absolute path matching utilizing os.listdir for robust OS-agnostic parsing
    log_files = []
    for file in os.listdir(log_dir):
        if file.startswith("[Ledger]_") and file.endswith(".txt"):
            log_files.append(os.path.join(log_dir, file))

    if not log_files:
        print(f"⚠️  [WARN] No historical logs found in {log_dir}. Proceeding with empty cache.")
        return skip_titles
    
    # Extract the single latest file based on the timestamped filename
    latest_log_file = max(log_files)
    
    # Parse ONLY the latest log file
    try:
        with open(latest_log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                try:
                    record = json.loads(line.strip())
                    title = record.get("title", "")
                    if title and record.get("relevance", 0) < 3:
                        skip_titles.add(normalize_title(title))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"⚠️  [ERROR] Corrupted ledger detected in {os.path.basename(latest_log_file)}: {e}")
            
    print(f"🗂️  [STATE] Loaded {len(skip_titles)} cached papers from the latest ledger: {os.path.basename(latest_log_file)}")
    return skip_titles


def save_run_log(all_evaluations):
    """
    Serializes LLM evaluation metadata into an append-only JSONL format for historical tracking.
    Outputs filenames with micro-batch precision: [Success]_Evaluations_YYYY-MM-DD.txt
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "Logs")
    os.makedirs(log_dir, exist_ok=True)
    
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(log_dir, f"[Success]_Evaluations_{today_str}.txt")
    
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            for record in all_evaluations:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        print(f"📝 [SYS] Evaluation matrix committed to disk: {log_path}")
    except Exception as e:
        print(f"⚠️  [ERROR] Failed to execute disk write operation: {e}")


def append_to_ledger(record):
    """
    Appends a single evaluation record to the daily ledger immediately.
    Ensures data persistence even if the pipeline crashes mid-run.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "Logs")
    os.makedirs(log_dir, exist_ok=True)
    
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(log_dir, f"[Ledger]_Evaluations_{today_str}.txt")
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f"⚠️  [ERROR] Critical IO Failure during real-time logging: {e}")




def generate_score_distribution_plot(all_evaluations):
    """
    Generates a bar chart visualizing the distribution of relevance scores.
    Saved as a PNG file in the Logs directory.
    """
    import matplotlib.pyplot as plt
    from collections import Counter

    # 1. Extract and count scores (Filtering out -1 from Stage 2)
    scores = [record.get("relevance", 0) for record in all_evaluations if record.get("relevance", -1) >= 0]
    if not scores:
        return None

    score_counts = Counter(scores)
    # Ensure all possible scores (0-5) are represented on the X-axis
    x_labels = [0, 1, 2, 3, 4, 5]
    y_values = [score_counts.get(s, 0) for s in x_labels]

    # 2. Setup Plot Aesthetics
    plt.figure(figsize=(10, 6), dpi=100)
    plt.style.use('ggplot')
    
    colors = ['#bdc3c7', '#95a5a6', '#3498db', '#f1c40f', '#e67e22', '#e74c3c'] # Gradient from Grey to Red
    bars = plt.bar(x_labels, y_values, color=colors, edgecolor='white', linewidth=1)

    # Add count labels on top of each bar
    for bar in bars:
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2., height + 0.1, f'{int(height)}', 
                 ha='center', va='bottom', fontsize=10, fontweight='bold', color='#2c3e50')

    # 3. Titles and Labels
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    plt.title(f"ArXiv Research Relevance Distribution ({today_str})", fontsize=14, fontweight='bold', pad=20)
    plt.xlabel("Relevance Score (0: Irrelevant -> 5: Core Match)", fontsize=11)
    plt.ylabel("Number of Papers", fontsize=11)
    plt.xticks(x_labels)
    plt.grid(axis='y', linestyle='--', alpha=0.7)

    # 4. Save Logic
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "Logs")
    os.makedirs(log_dir, exist_ok=True)
    
    plot_filename = f"[Success]_Distribution_{today_str}.png"
    plot_path = os.path.join(log_dir, plot_filename)
    
    plt.savefig(plot_path, bbox_inches='tight')
    plt.close() # Release memory
    print(f"📊 [SYS] Score distribution chart generated: {plot_path}")
    return plot_filename



# =====================================================================
# 3. Core Processing Pipeline (The Funnel)
# =====================================================================

def fetch_and_local_filter_arxiv():
    """
    Stage 1: Exploits arXiv API pagination to ingest the full 24-hour window,
    applying fast localized heuristic filtering (Regex/Length) to minimize token spend.
    
    Returns:
        tuple: (List of filtered feedparser objects, Integer of total raw papers fetched).
    """
    query = config['arxiv']['query']
    must_have_keywords = config['arxiv']['must_have_keywords']
    allowed_cs_subs = config['arxiv'].get('allowed_cs_subcategories', ["cs.AI", "cs.CV", "cs.LG", "cs.NE", "cs.GR"])
    limit = config['arxiv']['hard_limit']
    max_retries = config['arxiv']['max_retries']
    
    start_index = 0
    results_per_page = 100
    latest_paper_time = None
    cutoff_time = None
    
    raw_window_papers = []  
    all_filtered_papers = [] 

    print("📡 [NET] Initializing expansive arXiv API ingestion protocol...")

    # -- Sub-Phase 1.A: Time Window Exhaustion --
    while True:
        url = f'http://export.arxiv.org/api/query?search_query={urllib.parse.quote(query)}&sortBy=submittedDate&sortOrder=descending&start={start_index}&max_results={results_per_page}'
        feed = None
        
        # Implement exponential backoff/retry protocol for API resilience
        for attempt in range(max_retries):
            try:
                response = urllib.request.urlopen(url, timeout=config['arxiv']['timeout'])
                feed = feedparser.parse(response.read())
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(config['arxiv']['retry_delay'])
                else:
                    print(f"❌ [NET] Persistent connection failure at index {start_index}: {e}")
                    break 

        if not feed or not feed.entries:
            break

        # Dynamically establish the 24-hour chronological bounds based on the latest paper
        if latest_paper_time is None:
            latest_paper_time = datetime.datetime.fromtimestamp(mktime(feed.entries[0].published_parsed), datetime.timezone.utc)
            cutoff_time = latest_paper_time - datetime.timedelta(hours=24)
            print(f"📅 [SYS] Temporal anchor established: {latest_paper_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        time_boundary_reached = False
        for p in feed.entries:
            published_dt = datetime.datetime.fromtimestamp(mktime(p.published_parsed), datetime.timezone.utc)
            if published_dt < cutoff_time:
                time_boundary_reached = True
                break
            raw_window_papers.append(p)

        if time_boundary_reached:
            break
        start_index += results_per_page

    total_raw_count = len(raw_window_papers)
    print(f"🔍 [STAGE 1] Applying heuristic rulesets to {total_raw_count} ingested papers...")
    
    # Mount persistent caching ledger
    skip_titles = get_historical_skip_titles()

    # -- Sub-Phase 1.B: Local Heuristic Pruning --
    for p in raw_window_papers:
        clean_title = normalize_title(p.title)
        
        # Filter 1: Cache Check (Drop if historically evaluated)
        if clean_title in skip_titles:
            continue
            
        # Filter 2: Substantive Length Enforcement (Drop short workshop papers/abstracts)
        arxiv_comment = p.get('arxiv_comment', '')
        pages_match = re.search(r'(\d+)\s*pages?', arxiv_comment, re.IGNORECASE)
        if pages_match and int(pages_match.group(1)) < 5:
            continue 

        # Filter 3: Mandatory Keyword Intersection
        text = (p.title + " " + p.summary).lower()
        if not any(keyword.lower() in text for keyword in must_have_keywords):
            continue

        # Filter 4: Strict Categorical Constraint (Enforce CS subsets)
        tags = [tag.get('term', '') for tag in p.get('tags', [])]
        is_cs, has_allowed_sub, has_other_cs_sub, is_pure_cs_no_sub = False, False, False, False

        for tag in tags:
            if tag.startswith('cs.'):
                is_cs = True
                if tag in allowed_cs_subs:
                    has_allowed_sub = True
                else:
                    has_other_cs_sub = True
            elif tag == 'cs':  
                is_cs = True
                is_pure_cs_no_sub = True

        if not is_cs: continue
        if not has_allowed_sub and (has_other_cs_sub and not is_pure_cs_no_sub): continue

        all_filtered_papers.append(p)

    # -- Sub-Phase 1.C: Final Cap --
    final_count = len(all_filtered_papers)
    if final_count > limit:
        all_filtered_papers = all_filtered_papers[:limit]
        final_count = limit
        
    pct = (final_count / total_raw_count * 100) if total_raw_count > 0 else 0
    print(f"🎯 [STAGE 1 OUTCOME] Heuristic execution complete. Maintained: {final_count}/{total_raw_count} ({pct:.1f}%)")

    return all_filtered_papers, total_raw_count


def pre_filter_by_titles(papers, total_raw_count):
    """
    Stage 2: High-velocity semantic pre-filter using an optimized LLM chat model.
    Passes titles to the model to discard explicit noise before costly abstract evaluations.
    
    Returns:
        tuple: (List of selected papers, List of rejected papers).
    """
    if not papers:
        return [], [], 0

    model = config['llm']['model_reasoning'] 
    print(f"\n🧠 [STAGE 2] Executing semantic title pre-filter across {len(papers)} heuristics-passed candidates...")

    title_list_str = ""
    for idx, p in enumerate(papers):
        title_list_str += f"{idx}. {p.title.replace(chr(10), ' ')}\n"

    # Compile prompt variables
    core_list = ", ".join(config['criteria']['research_keywords']['core_domains'])
    neg_list = ", ".join(config['criteria']['negative_prompt'])
    focus = config['criteria'].get('current_focus', "None")

    prompt = config['prompts']['pre_filter_prompt']
    prompt = prompt.replace("{RESEARCH_KEYWORDS_CORE}", core_list)
    prompt = prompt.replace("{NEGATIVE_PROMPT}", neg_list)
    prompt = prompt.replace("{CURRENT_FOCUS}", focus)
    prompt = prompt.replace("{TITLE_LIST}", title_list_str)

    try:
        # Catch content and tokens
        res, p_tok, c_tok = call_deepseek([{"role": "user", "content": prompt}], model, 0.1, True)
        tokens_consumed = p_tok + c_tok
        
        clean_res = extract_json_from_text(res)
        selected_indices = json.loads(clean_res)
        
        if isinstance(selected_indices, list):
            selected_papers = []
            rejected_papers = []
            for i, p in enumerate(papers):
                if i in selected_indices:
                    selected_papers.append(p)
                else:
                    rejected_papers.append(p)
            
            # --- Enhanced X/Y Telemetry ---
            maintained_count = len(selected_papers)
            pct = (maintained_count / total_raw_count * 100) if total_raw_count > 0 else 0
            print(f"✅ [STAGE 2 OUTCOME] Pre-filter complete. Maintained: {maintained_count}/{total_raw_count} ({pct:.1f}%)")
            
            return selected_papers, rejected_papers, tokens_consumed
            
    except Exception as e:
        print(f"⚠️  [ERROR] Pre-filter routine failure: {e}. Executing bypass.")
        return papers, [], 0

    return papers, [], 0


def evaluate_and_filter_papers(papers, total_raw_count):
    """
    Stage 3: Deep quantitative analysis via Chain-of-Thought (CoT) reasoning model.
    Scores abstracts on rigorous metrics (Relevance, Novelty, Venue Bonus).
    
    Returns:
        tuple: (List of dictionaries containing paper metadata/scores, List of all evaluation records for ledger).
    """
    if not papers:
        return [], [], 0
        
    model = config['llm']['model_reasoning']
    print(f"\n🧠 [STAGE 3] Initiating advanced reasoning protocols ({model}) for deep semantic evaluation...")
    
    filtered_results = []
    all_evaluations = []
    stage_total_tokens = 0

    # Compile matrix variables
    core_list = ", ".join(config['criteria']['research_keywords']['core_domains'])
    method_list = ", ".join(config['criteria']['research_keywords']['methodologies'])
    neg_list = ", ".join(config['criteria']['negative_prompt'])
    focus = config['criteria'].get('current_focus', "None")
    tracked_authors_list = ", ".join(config['criteria'].get('tracked_authors', []))
    top_venues_dict_str = json.dumps(config['criteria'].get('top_venues', {}), ensure_ascii=False)

    for idx, p in enumerate(papers, 1):
        clean_title = p.title.replace('\n', ' ')
        print(f"\n 🔍 Evaluating [{idx}/{len(papers)}]:\n    Title: {clean_title}")
        
        authors = ", ".join([author.name for author in p.authors])
        arxiv_comment = p.get('arxiv_comment', 'Not specified')
        
        prompt_template = config['prompts']['evaluate_prompt']
        prompt = (
            prompt_template.replace("{RESEARCH_KEYWORDS_CORE}", core_list)
            .replace("{RESEARCH_KEYWORDS_METHOD}", method_list)
            .replace("{NEGATIVE_PROMPT}", neg_list)
            .replace("{CURRENT_FOCUS}", focus)
            .replace("{TOP_VENUES}", top_venues_dict_str) 
            .replace("{TRACKED_AUTHORS}", tracked_authors_list)
            .replace("{TITLE}", p.title)
            .replace("{AUTHORS}", authors)
            .replace("{SUMMARY}", p.summary)
            .replace("{REMARKS}", arxiv_comment)      
        )

        try:
            # Catch token usage per paper
            response, p_tok, c_tok = call_deepseek([{"role": "user", "content": prompt}], model, config['llm']['temp_reasoning'], True)
            stage_total_tokens += (p_tok + c_tok)
            clean_json_str = extract_json_from_text(response)
            data = json.loads(clean_json_str)

            # Safely unpack list or float structures
            raw_score = data.get("relevance", 0)
            final_score = int(raw_score[0]) if isinstance(raw_score, list) and len(raw_score) > 0 else int(raw_score)

            raw_bonus = data.get("bonus_score", 0.0)
            bonus_score = float(raw_bonus[0]) if isinstance(raw_bonus, list) and len(raw_bonus) > 0 else float(raw_bonus)
            
            rationale = data.get("thought_trace", "No rationale provided.")

            # Record ledger metrics regardless of survival to map historical memory
            evaluation_record = {
                "title": clean_title,
                "relevance": final_score,
                "bonus_score": bonus_score,
                "thought_trace": rationale,
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
            }
            append_to_ledger(evaluation_record)
            all_evaluations.append(evaluation_record)

            if final_score > 0:
                data.update({
                    "relevance": final_score, 
                    "bonus_score": bonus_score, 
                    "arxiv_url": p.link,
                    "title": p.title,
                    "authors_and_affiliations": authors,
                    "submission_venue": f"arXiv Comment: {arxiv_comment}"
                })
                filtered_results.append(data)
                print(f"    ✅ Action: KEPT  |  Base Score: {final_score}  |  Bonus: {bonus_score}")
                print(f"    💡 Reason: {rationale}")
            else:
                print(f"    ❌ Action: DROP  |  Base Score: {final_score}")
                print(f"    💡 Reason: {rationale}")

            time.sleep(1) # Impose generic throttle to honor upstream API constraints

        except Exception as e:
            print(f"    ⚠️  [ERROR] Payload parsing failed for '{clean_title}'. Details: {e}")

    # --- Enhanced X/Y Telemetry ---
    final_maintained = len(filtered_results)
    pct = (final_maintained / total_raw_count * 100) if total_raw_count > 0 else 0
    print(f"\n🏁 [STAGE 3 OUTCOME] Deep evaluation complete. Maintained: {final_maintained}/{total_raw_count} ({pct:.1f}%)")

    return filtered_results, all_evaluations, stage_total_tokens


def beautify_to_markdown(top_papers):
    """
    Stage 5A: Delegates raw JSON data to an LLM to render professional academic Markdown.
    """
    if not top_papers:
        return "", 0

    model = config['llm']['model_reasoning']
    print(f"\n🎨 [STAGE 5A] Invoking UI logic ({model}) to synthesize Markdown aesthetics for {len(top_papers)} papers...")
    
    raw_content = json.dumps(top_papers, ensure_ascii=False, indent=2)
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    prompt = config['prompts']['beautify_prompt']
    prompt = prompt.replace("{TODAY_STR}", today_str)
    prompt = prompt.replace("{TOP_K}", str(len(top_papers)))
    prompt = prompt.replace("{RAW_CONTENT}", raw_content)

    res, p_tok, c_tok = call_deepseek([{"role": "user", "content": prompt}], model, config['llm']['temp_chat'], False)
    return res, (p_tok + c_tok)


def send_beautiful_email(md_content, extra_html=""):
    """
    Stage 5B: Compiles Markdown into responsive CSS/HTML structures and triggers PushPlus webhook.
    """
    print("📲 [STAGE 5B] Translating Markdown to DOM nodes and initializing PushPlus webhook payload...")
    
    html_body = markdown.markdown(md_content, extensions=['extra', 'nl2br'])
    
    # Restructure generic HTML into visually distinct modular cards
    parts = html_body.split('<h3>')
    processed_html = parts[0] 
    for part in parts[1:]:
        processed_html += f'<div class="paper-card"><h3>{part}</div>'

    processed_html += extra_html
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    final_html = f"""
    <html>
        <head>
            <style>
                * {{ box-sizing: border-box; }} 
                body {{ font-family: -apple-system, system-ui, sans-serif; line-height: 1.6; color: #333; max-width: 100%; margin: 0; padding: 10px; }}
                h1 {{ border-bottom: 2px solid #2c3e50; padding-bottom: 10px; color: #2c3e50; text-align: center; font-size: 1.4em; }}
                h3 {{ color: #2980b9; margin-top: 0; border-left: 4px solid #3498db; padding-left: 10px; font-size: 1.15em; line-height: 1.3; }}
                ul {{ list-style-type: none; padding-left: 0; margin-bottom: 0; }}
                li {{ margin-bottom: 8px; font-size: 0.95em; word-wrap: break-word; }}
                ul ul {{ padding-left: 15px; list-style-type: disc; margin-top: 10px; }}
                a {{ color: #e74c3c; text-decoration: none; font-weight: bold; word-break: break-all; }}
                code {{ background-color: #e8f0fe; color: #1a73e8; padding: 2px 5px; border-radius: 4px; font-size: 0.9em; }}
                .paper-card {{ background-color: #f8f9fa; border: 1px solid #e9ecef; padding: 15px; margin-bottom: 20px; border-radius: 8px; width: 100%; clear: both; }}
                
                details {{ background-color: #f8f9fa; border: 1px dashed #bdc3c7; border-radius: 8px; padding: 10px; margin-bottom: 20px; }}
                summary {{ color: #7f8c8d; font-size: 0.9em; font-weight: bold; cursor: pointer; outline: none; }}
                .runner-up-item {{ font-size: 0.85em; color: #555; margin-top: 12px; border-bottom: 1px solid #eee; padding-bottom: 8px; }}
                .runner-up-item:last-child {{ border-bottom: none; }}
            </style>
        </head>
        <body>
            {processed_html} 
            <br><hr>
            <p style="font-size: 12px; color: #7f8c8d; text-align: center;">Report automatically compiled and rendered by Hongyu AI Assistant</p>
        </body>
    </html>
    """

    payload = {
        "token": config['auth']['pushplus_token'],
        "title": f"[Daily arXiv] 精选论文速递 ({today_str})",
        "content": final_html,
        "template": "html"
    }

    try:
        response = requests.post("http://www.pushplus.plus/send", json=payload, timeout=20)
        res_json = response.json()
        if res_json.get("code") == 200:
            print("🎉 [SUCCESS] Webhook transmitted successfully! Please check WeChat.")
        else:
            print(f"⚠️  [API ERROR] PushPlus responded with anomalous payload: {res_json}")
            raise Exception(f"PushPlus Payload Error: {res_json}")
    except Exception as e:
        print(f"❌ [CRITICAL] Webhook execution failed: {e}")
        raise e


# =====================================================================
# [4. Main Execution Controller - Pipeline Director]
# =====================================================================
def run_job():
    """
    Orchestrates the pipeline and compiles execution telemetry for the final report.
    """
    # Start the master clock for performance profiling
    start_time = time.time()
    total_tokens = 0
    
    # Initialize telemetry placeholders to prevent reference errors
    duration_min = 0.0
    token_w = 0.0
    
    try:
        # --- Stage 1: Hard Heuristics ---
        filtered_stage1, x_count = fetch_and_local_filter_arxiv()
        if x_count == 0:
            print("⚠️  [HALT] arXiv fetched 0 papers.")
            return -1
        if not filtered_stage1:
            print("ℹ️  [SYS] 0 papers survived heuristics.")
            return 3
        y_count = len(filtered_stage1)

        # --- Stage 2: Semantic Title Scan ---
        pre_filtered_pool, rejected_by_prefilter, tokens_s2 = pre_filter_by_titles(filtered_stage1, x_count)
        total_tokens += tokens_s2
        star_count = len(pre_filtered_pool)
        
        # Real-time persistence for noise detected at Stage 2
        prefilter_evaluations = []
        for p in rejected_by_prefilter:
            rej_record = {
                "title": p.title.replace('\n', ' '),
                "relevance": -1,
                "bonus_score": 0.0,
                "thought_trace": "Rejected at Stage 2 (Semantic Pre-filter)",
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
            }
            # Commit to disk immediately
            append_to_ledger(rej_record)
            prefilter_evaluations.append(rej_record)

        # --- Stage 3: LLM CoT Scoring ---
        # evaluate_and_filter_papers inside should now use append_to_ledger(evaluation_record) per iteration
        scored_papers, deep_evaluations, tokens_s3 = evaluate_and_filter_papers(pre_filtered_pool, x_count)
        total_tokens += tokens_s3
        
        # Combine memory objects for visualization/sorting
        all_evaluations = prefilter_evaluations + deep_evaluations

        if not scored_papers:
            print("ℹ️  [SYS] Null set produced post-LLM reasoning. Ledger already committed.")
            return 3

        # --- Stage 4: Sorting & Ranking ---
        top_k = config['filter']['top_k_papers']
        scored_papers.sort(key=lambda x: (x.get('relevance', 0), x.get('bonus_score', 0.0)), reverse=True)
        
        top_papers = scored_papers[:top_k]
        runner_ups = [p for p in scored_papers[top_k:] if p.get('relevance', 0) >= 3]
        z_count = len(top_papers)

        # --- Stage 5: Generation & Dispatch ---
        md_content, tokens_s5 = beautify_to_markdown(top_papers)
        total_tokens += tokens_s5

        # --- Final Telemetry Computation ---
        token_w = round(total_tokens / 10000, 2)
        duration_min = round((time.time() - start_time) / 60, 2)
        ratio = round((z_count / x_count * 100), 2) if x_count > 0 else 0

        # Fetch Chart URL
        dist_chart_url = get_score_distribution_url(all_evaluations)
        chart_html = f'<div style="margin-top: 15px; text-align: center;"><img src="{dist_chart_url}" style="max-width: 100%; border-radius: 8px; border: 1px solid #eee;" /></div>' if dist_chart_url else ""
        
        # --- Build Stats UI Box (Apple Minimalism Style) ---
        # --- Build Stats UI Box (Apple Minimalism - Chinese Version) ---
        stats_html = f"""
        <div style="background-color: #ffffff; border: 1px solid #f2f2f2; color: #86868b; padding: 24px; border-radius: 16px; margin: 30px 0; font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif; font-size: 0.88em; line-height: 1.6; box-shadow: 0 4px 20px rgba(0,0,0,0.04);">
            <div style="color: #1d1d1f; font-size: 1.1em; font-weight: 600; letter-spacing: 0.05em; margin-bottom: 20px; text-align: center;">任务执行简报</div>
            
            <div style="margin-bottom: 8px; color: #1d1d1f;">
                • 今日 arXiv 相关领域发现：<span style="font-weight: 600;">{x_count} 篇</span>
            </div>
            
            <div style="border-left: 1px solid #d2d2d7; margin-left: 6px; padding-left: 16px;">
                <div style="margin-bottom: 4px;">└ 经学术硬筛选通过：<span style="font-weight: 600;">{y_count} 篇</span></div>
                <div style="margin-left: 16px; border-left: 1px solid #d2d2d7; padding-left: 16px;">
                    <div style="margin-bottom: 4px;">└ 经 AI 语义初筛保留：<span style="font-weight: 600;">{star_count} 篇</span></div>
                    <div style="margin-left: 16px; border-left: 1px solid #3498db; padding-left: 16px; color: #3498db; font-weight: 600;">
                        └ 最终深度精选：{z_count} 篇
                    </div>
                </div>
            </div>

            <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #f2f2f2; display: flex; justify-content: space-between; font-size: 0.75em; color: #afafb6; letter-spacing: 0.05em;">
                <span>文献转化率: <strong>{ratio}%</strong></span>
                <span>TOKENS: <strong>{token_w}W Tokens</strong></span>
                <span>运行时长: <strong>{duration_min}min</strong></span>
            </div>
            
            <div style="margin-top: 20px;">
                <img src="{dist_chart_url}" style="width: 100%; height: auto; display: block;" />
                <div style="text-align: center; font-size: 0.7em; color: #d2d2d7; margin-top: 8px; letter-spacing: 0.2em;">文献相关度分布趋势</div>
            </div>
        </div>
        """

        runner_up_html = ""
        if runner_ups:
            runner_up_html += '\n<details>\n'
            runner_up_html += f'  <summary style="color: #3498db; font-weight: bold; cursor: pointer;">📂 点击展开：另外 {len(runner_ups)} 篇高分候选文献 </summary>\n'
            runner_up_html += '  <div style="margin-top: 10px; border-top: 1px solid #eee; padding-top: 10px;">\n'
            for rp in runner_ups:
                runner_up_html += f'    <div class="runner-up-item" style="margin-bottom: 12px; font-size: 0.85em;">\n'
                runner_up_html += f'      <strong style="color: #2c3e50;">[Score: {rp.get("relevance")}]</strong> <a href="{rp.get("arxiv_url")}" style="color: #2980b9; text-decoration: none;">{rp.get("title")}</a><br>\n'
                runner_up_html += f'      <span style="color: #e67e22;">💡 理由:</span> <span style="color: #7f8c8d;">{rp.get("thought_trace")}</span>\n'
                runner_up_html += f'    </div>\n'
            runner_up_html += '  </div>\n</details>\n'

        # Dispatch Final Report
        send_beautiful_email(md_content, runner_up_html + stats_html)

        # Local distribution plot save (optional backup)
        try:
            generate_score_distribution_plot(all_evaluations)
        except Exception as e:
            print(f"⚠️  [ERROR] Local plotting failed: {e}")

        print(f"🏁 [SYS] Pipeline sequence fully finalized in {duration_min} min. Exiting (Code 1).")
        return 1
        
    except Exception as e:
        print(f"❌ [FATAL] Stack integrity breached: {e}")
        import traceback
        traceback.print_exc() 
        return -2


if __name__ == "__main__":
    run_job()
