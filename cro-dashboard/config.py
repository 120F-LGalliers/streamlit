# config.py — non-sensitive configuration, safe to commit to GitHub

CLIENTS = {
    "TSB": {
        "harvest_project_id": 46831914,
        "pm_tool": "trello",
        "velocity_target_per_month": 6,
        "icon": "🏦",
    },
    "Dominos": {
        "harvest_project_id": 46954668,
        "pm_tool": "monday",
        "velocity_target_per_month": 4,
        "icon": "🍕",
    },
    "Tesco Mobile": {
        "harvest_project_id": 46647068,
        "pm_tool": "jira_tesco",
        "velocity_target_per_month": 8,
        "icon": "📱",
    },
    "Avis": {
        "harvest_project_ids": [47029297, 47725794],  # two Harvest projects, combined view
        "pm_tool": "jira_avis",
        "velocity_target_per_month": 2,
        "icon": "🚗",
    },
}

# Harvest — task ID to function group mapping
TASK_GROUPS = {
    22783262: "Dev",      22783261: "Dev",
    22783286: "Analysis", 22783287: "Analysis", 22783289: "Analysis", 22783288: "Analysis",
    22783264: "QA",       23771418: "QA",
    22782955: "CRO/PM/BA", 22782953: "CRO/PM/BA", 22782952: "CRO/PM/BA",
    22782954: "CRO/PM/BA", 22782958: "CRO/PM/BA",
    22783265: "Design",
    16091205: "Workshop",
}

# Harvest — user name to group fallback (for entries where task ID isn't in TASK_GROUPS)
TEAM_MEMBERS = {
    "Echo Dev - Lisa":      "Dev",
    "Echo QA - Afrin":      "QA",
    "Optimisation Dev":     "Dev",
    "Optimisation QA":      "QA",
    "TM Design":            "Design",
    "Ariful Kawshiq (QA)":  "QA",
    "Abdul Khalek (dev)":   "Dev",
}

# Harvest — monthly hour budgets per project per function group
PROJECT_BUDGETS = {
    46647068: {"Analysis": 97,    "CRO/PM/BA": 88,     "Dev": 165,  "QA": 100, "Design": 100, "Workshop": 16.7},  # Tesco Mobile
    46831914: {"Analysis": 31.6,  "CRO/PM/BA": 41.4,   "Dev": 54.2, "QA": 27},                                   # TSB
    46954668: {"Analysis": 26.25, "CRO/PM/BA": 49.275,  "Dev": 63.75,"QA": 22.5},                                 # Dominos
    47029297: {"CRO/PM/BA": 33,   "Dev": 83,            "QA": 50},                                                 # Avis (project 1)
    47725794: {"CRO/PM/BA": 33,   "Dev": 83,            "QA": 50},                                                 # Avis (project 2)
}

# Harvest — human-readable labels for individual project IDs (used in multi-project breakdowns)
PROJECT_LABELS = {
    47029297: "Avis Project 1",   # update with actual Harvest project names
    47725794: "Avis Project 2",
}

# Jira — Tesco Mobile
JIRA_TESCO_TARGET_EPIC   = "EAOA-260"
JIRA_TESCO_TARGET_COLUMN = ["Ready to Publish", "Run"]

# Jira — Avis
JIRA_AVIS_BOARD_ID      = 5025       # agile board ID (not used in current JQL mode)
JIRA_AVIS_TARGET_COLUMN = "in testing"
JIRA_AVIS_LABEL_FILTER  = "120Feet"  # only count stories with this label

# Trello — TSB
TRELLO_TARGET_COLUMNS = ["Ready to Launch", "Full Launch"]

# Monday.com — Dominos
MONDAY_TARGET_STATUSES = ["Ready to Publish", "Next Live"]
MONDAY_ACTIVITY_TYPE_COLUMN_TITLE = "Activity Type"  # exact column title in the Monday board
MONDAY_AB_TYPE_LABEL = "A/B"                          # exact label text for A/B tests
