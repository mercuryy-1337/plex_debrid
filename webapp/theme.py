"""
Theme and styling for pd_reloaded NiceGUI frontend.
"""

from nicegui import ui

# Color palette
COLORS = {
    "primary": "#E5A00D",       # Plex gold
    "secondary": "#1F2937",     # Dark gray
    "accent": "#CC7B19",        # Darker gold
    "background": "#0F1117",    # Very dark
    "surface": "#1A1D26",       # Card background
    "surface_light": "#252830", # Lighter surface
    "text": "#E5E7EB",          # Light text
    "text_muted": "#9CA3AF",    # Muted text
    "success": "#10B981",       # Green
    "warning": "#F59E0B",       # Amber
    "error": "#EF4444",         # Red
    "info": "#3B82F6",          # Blue
}

CSS = """
<style>
    :root {
        --primary: #E5A00D;
        --bg: #0F1117;
        --surface: #1A1D26;
        --surface-light: #252830;
        --text: #E5E7EB;
        --text-muted: #9CA3AF;
    }
    
    body {
        background-color: var(--bg) !important;
        color: var(--text) !important;
    }
    
    .q-card {
        background-color: var(--surface) !important;
        border: 1px solid rgba(255,255,255,0.06) !important;
        border-radius: 10px !important;
        padding: 0 !important;
    }
    
    .q-card__section {
        padding: 12px 16px !important;
    }
    
    .q-drawer {
        background-color: var(--surface) !important;
    }
    
    .q-header {
        background-color: var(--surface) !important;
        border-bottom: 1px solid rgba(255,255,255,0.06) !important;
    }
    
    .q-table {
        background-color: var(--surface) !important;
    }
    
    .q-table thead th {
        color: var(--text-muted) !important;
        font-weight: 600 !important;
    }

    /* Consistent card padding */
    .q-card.p-3 { padding: 12px !important; }
    .q-card.p-4 { padding: 16px !important; }
    
    /* Tighter expansion panels */
    .q-expansion-item .q-item {
        padding: 6px 12px !important;
        min-height: 40px !important;
    }
    .q-expansion-item__content .q-card {
        border: none !important;
        border-radius: 0 !important;
    }
    
    /* Even field spacing */
    .q-field {
        margin-bottom: 0 !important;
    }
    .q-field--outlined .q-field__control {
        border-radius: 6px !important;
    }
    .q-field--dense .q-field__control {
        min-height: 36px !important;
    }
    
    /* Tighter separators */
    .q-separator {
        margin: 8px 0 !important;
    }
    
    .stat-card {
        background: linear-gradient(135deg, var(--surface) 0%, var(--surface-light) 100%) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 12px !important;
        padding: 16px !important;
        transition: transform 0.2s ease, box-shadow 0.2s ease !important;
    }
    
    .stat-card:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 8px 24px rgba(0,0,0,0.3) !important;
    }
    
    .content-card {
        background: var(--surface) !important;
        border: 1px solid rgba(255,255,255,0.06) !important;
        border-radius: 10px !important;
        overflow: hidden !important;
        transition: transform 0.2s ease, box-shadow 0.2s ease !important;
        padding: 0 !important;
    }
    
    .content-card:hover {
        transform: translateY(-3px) !important;
        box-shadow: 0 8px 24px rgba(0,0,0,0.35) !important;
    }
    
    .sidebar-item {
        border-radius: 8px !important;
        margin: 2px 8px !important;
        transition: background-color 0.2s ease !important;
    }
    
    .sidebar-item:hover {
        background-color: rgba(229, 160, 13, 0.1) !important;
    }
    
    .sidebar-item.active {
        background-color: rgba(229, 160, 13, 0.15) !important;
        color: #E5A00D !important;
    }
    
    .onboarding-step {
        background: var(--surface) !important;
        border: 1px solid rgba(255,255,255,0.06) !important;
        border-radius: 16px !important;
        padding: 32px !important;
    }
    
    .badge-anime {
        background: linear-gradient(135deg, #7C3AED, #DB2777) !important;
        color: white !important;
        padding: 2px 8px !important;
        border-radius: 4px !important;
        font-size: 0.7rem !important;
        font-weight: 600 !important;
    }
    
    .badge-movie {
        background: linear-gradient(135deg, #2563EB, #7C3AED) !important;
        color: white !important;
        padding: 2px 8px !important;
        border-radius: 4px !important;
        font-size: 0.7rem !important;
        font-weight: 600 !important;
    }
    
    .badge-show {
        background: linear-gradient(135deg, #059669, #2563EB) !important;
        color: white !important;
        padding: 2px 8px !important;
        border-radius: 4px !important;
        font-size: 0.7rem !important;
        font-weight: 600 !important;
    }
    
    .release-cached {
        color: #10B981 !important;
    }
    
    .release-uncached {
        color: #EF4444 !important;
    }
    
    .q-btn--flat {
        text-transform: none !important;
    }
    
    .nicegui-content {
        padding: 0 !important;
    }
    
    .q-tab-panel {
        padding: 12px 0 !important;
        background: transparent !important;
    }

    .q-tab-panels {
        background: transparent !important;
    }

    .q-tab {
        padding: 4px 12px !important;
        min-height: 40px !important;
    }
    
    /* Tighter gaps in columns and rows */
    .gap-6 { gap: 16px !important; }
    .gap-4 { gap: 12px !important; }
    .gap-3 { gap: 8px !important; }
    
    /* Even page padding */
    .p-6 { padding: 16px !important; }
    .p-4 { padding: 12px 16px !important; }
    .p-3 { padding: 8px 12px !important; }
    
    .mt-4 { margin-top: 12px !important; }
    .mb-6 { margin-bottom: 12px !important; }
    .my-4 { margin-top: 8px !important; margin-bottom: 8px !important; }
    .my-2 { margin-top: 4px !important; margin-bottom: 4px !important; }
    
    .log-entry {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.8rem !important;
        padding: 3px 0 !important;
        border-bottom: 1px solid rgba(255,255,255,0.03) !important;
    }
    
    .status-running {
        color: #10B981 !important;
    }
    
    .status-stopped {
        color: #EF4444 !important;
    }
    
    .status-idle {
        color: #9CA3AF !important;
    }
    
    /* Scrollbar styling */
    ::-webkit-scrollbar {
        width: 6px;
        height: 6px;
    }
    ::-webkit-scrollbar-track {
        background: var(--bg);
    }
    ::-webkit-scrollbar-thumb {
        background: var(--surface-light);
        border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: var(--text-muted);
    }
</style>
"""


def apply_theme():
    """Apply the pd_reloaded theme to the page."""
    ui.add_head_html(CSS)
    ui.add_head_html(
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">'
    )
    ui.query("body").style("font-family: 'Inter', sans-serif")
