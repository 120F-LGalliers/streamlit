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
    "Ripe Insurance": {
        "harvest_project_id": 47862901,
        "pm_tool": "trello_ripe",
        "velocity_target_per_month": 1,
        "velocity_period": "quarter",
        "icon": "🛡️",
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
    47862901: {"CRO/PM/BA": 3,    "Dev": 7,             "QA": 4},                                                  # Ripe Insurance (monthly = quarterly ÷ 3)
}

# Harvest — human-readable labels for individual project IDs (used in multi-project breakdowns)
PROJECT_LABELS = {
    47029297: "Avis Project 1",   # update with actual Harvest project names
    47725794: "Avis Project 2",
}

# Jira — Tesco Mobile
JIRA_TESCO_TARGET_EPIC    = "EAOA-260"
JIRA_TESCO_TARGET_COLUMNS = ["Ready to Publish", "Run"]  # count tickets reaching either column

# Jira — Tesco Mobile cycle time transitions (ordered pipeline stages)
JIRA_TESCO_TRANSITIONS = [
    ("To Do", "Research / Solution"),
    ("Research / Solution", "Ready for Estimation"),
    ("Ready for Estimation", "Ready to Build"),
    ("Ready to Build", "Validation"),
    ("Validation", "Ready to Publish"),
    ("Ready to Publish", "Run"),
]

# Jira — Avis
JIRA_AVIS_BOARD_ID      = 5025       # agile board ID (not used in current JQL mode)
JIRA_AVIS_TARGET_COLUMN = "in testing"
JIRA_AVIS_LABEL_FILTER  = "120Feet"  # only count stories with this label

# Jira — Avis cycle time transitions (ordered pipeline stages)
JIRA_AVIS_TRANSITIONS = [
    ("Refinement in progress (migrated)", "Ready for Build (Migrated)"),
    ("Ready for Build (Migrated)", "In Progress"),
    ("In Progress", "Blocked"),
    ("Blocked", "In Testing"),
]

# Trello — TSB
TRELLO_TARGET_COLUMNS = ["Ready to Launch", "Full Launch"]
TRELLO_FULL_LAUNCH_COLUMN = "Full Launch"
TRELLO_WIN_COLUMNS = ["Winners", "Live to 100% of Traffic (via Target)"]

# Trello — TSB cycle time transitions (ordered pipeline stages)
TRELLO_TRANSITIONS = [
    ("Test Plan", "TSB Approval"),
    ("TSB Approval", "Ready for Estimation"),
    ("Ready for Estimation", "QA"),
    ("QA", "Validation"),
    ("Validation", "Ready to Launch"),
]

# Monday — Dominos cycle time transitions (ordered pipeline stages)
MONDAY_TRANSITIONS = [
    ("Continuous Discovery", "Estimation"),
    ("Estimation", "Ready to Build"),
    ("Ready to Build", "Validation/UAT"),
    ("Validation/UAT", "Ready to Publish"),
    ("Ready to Publish", "Live"),
]

# Monday.com — Dominos
MONDAY_TARGET_STATUSES = ["Ready to Publish", "Next Live"]
MONDAY_ACTIVITY_TYPE_COLUMN_TITLE = "Activity Type"  # exact column title in the Monday board
MONDAY_AB_TYPE_LABEL = "A/B"                          # exact label text for A/B tests
MONDAY_WIN_COLUMNS       = ["Always On", "Winners"]
MONDAY_CONCLUDED_COLUMNS = ["Always On", "Winners", "Done", "Archived", "Losers"]

# Jira — Tesco Mobile win rate
JIRA_TESCO_WIN_STATUSES       = ["100% Live", "Winners"]
JIRA_TESCO_CONCLUDED_STATUSES = ["100% Live", "Winners", "Inconclusive", "Losers", "Done"]

# Jira — Avis win rate
JIRA_AVIS_WIN_STATUSES       = ["Ready for Deployment"]
JIRA_AVIS_CONCLUDED_STATUSES = ["Ready for Deployment", "Done"]

# Trello — Ripe Insurance
RIPE_TRELLO_BOARD_ID         = "PkATiCpH"
RIPE_TRELLO_TARGET_COLUMNS   = ["Next Live", "Live"]
RIPE_TRELLO_WIN_COLUMNS      = ["Winners"]
RIPE_TRELLO_CONCLUDED_COLUMNS = ["Winners", "Losers", "Inconclusive"]
RIPE_TRELLO_TRANSITIONS      = [
    ("Continuous Discovery", "Ready to Build"),
    ("Ready to Build", "Validation"),
    ("Validation", "Next Live"),
    ("Validation", "Live"),
]
