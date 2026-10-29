app_name = "fhir_gateway"
app_title = "FHIR Gateway"
app_publisher = "Medtronic Labs"
app_description = "A FHIR R4 REST API over Frappe Healthcare clinical data, for consumption by external systems such as OpenHIM."
app_email = "admin@medtroniclabs.org"
app_license = "gpl-3.0"

# Apps
# ------------------

required_apps = ["healthcare"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "fhir_gateway",
# 		"logo": "/assets/fhir_gateway/logo.png",
# 		"title": "FHIR Gateway",
# 		"route": "/fhir_gateway",
# 		"has_permission": "fhir_gateway.api.permission.has_app_permission",
# 	}
# ]

# The dock, the rail down the left of the desk, is a document rather than a hook. Author it in
# Manage Dock on a developer-mode site and press Export to App, and it is written to
# `fhir_gateway/dock/fhir_gateway/fhir_gateway.json` for git to carry. An app that ships none has no
# rail: its sidebar gets a switcher in the header instead.
#
# A companion app, one that extends a host app rather than standing on its own, says so with
# `mount_on` on that same record, and its entries are appended to the host's rail. Mounting keeps
# the companion off the apps screen, so it takes precedence over any add_to_apps_screen above.

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/fhir_gateway/css/fhir_gateway.css"
# app_include_js = "/assets/fhir_gateway/js/fhir_gateway.js"

# include js, css files in header of web template
# web_include_css = "/assets/fhir_gateway/css/fhir_gateway.css"
# web_include_js = "/assets/fhir_gateway/js/fhir_gateway.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "fhir_gateway/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_kanban_js = {"doctype" : "public/js/doctype_kanban.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "fhir_gateway/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# FHIR REST API
# ------------------
# Owns the /fhir/* path space with a custom page renderer -- website_route_rules only rewrites to
# Frappe's own HTML page renderers (DocumentPage/WebFormPage/etc, see website/path_resolver.py),
# none of which return raw JSON, and /api/* requests never reach the website router at all
# (frappe/app.py dispatches them separately before get_response() runs). page_renderer is the
# real, intended extensibility point for an app that wants to own an arbitrary path and return a
# raw werkzeug Response -- see renderer.py.
page_renderer = ["fhir_gateway.renderer.FHIRPageRenderer"]

# Fixtures
# ------------------
# "FHIR Integration" role + its read-only Custom DocPerm grants on exactly the three doctypes this
# gateway exposes (Patient, Patient Encounter, Observation) -- nothing else is reachable through
# this role even if a client asks for it. Filtered (not bare doctype names): Role and Custom DocPerm
# both have hundreds of unrelated site-wide records, so a bare name would make `bench
# export-fixtures` dump every role/permission on the site into this app.
fixtures = [
	{"dt": "Role", "filters": [["name", "in", ["FHIR Integration", "FHIR Sync Administrator"]]]},
	{"dt": "Custom DocPerm", "filters": [["role", "=", "FHIR Integration"]]},
]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "fhir_gateway.utils.jinja_methods",
# 	"filters": "fhir_gateway.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "fhir_gateway.install.before_install"
# after_install = "fhir_gateway.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "fhir_gateway.uninstall.before_uninstall"
# after_uninstall = "fhir_gateway.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "fhir_gateway.utils.before_app_install"
# after_app_install = "fhir_gateway.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "fhir_gateway.utils.before_app_uninstall"
# after_app_uninstall = "fhir_gateway.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "fhir_gateway.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "fhir_gateway.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["fhir_gateway.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Outbound sync: every submitted clinical record is mapped to FHIR and queued for configured
# destinations (fhir_gateway/sync/). Must stay in step with sync.triggers._BUILDERS.
doc_events = {
	doctype: {"on_submit": "fhir_gateway.sync.triggers.on_submit"}
	for doctype in ("Patient Encounter", "Observation", "Vital Signs", "Medication Request", "Service Request")
}

scheduler_events = {
	"cron": {
		# safety net for the near-real-time job enqueued after each submit; also drives retries
		"*/5 * * * *": ["fhir_gateway.sync.dispatcher.sweep_due_deliveries"],
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"fhir_gateway.tasks.all"
# 	],
# 	"daily": [
# 		"fhir_gateway.tasks.daily"
# 	],
# 	"hourly": [
# 		"fhir_gateway.tasks.hourly"
# 	],
# 	"weekly": [
# 		"fhir_gateway.tasks.weekly"
# 	],
# 	"monthly": [
# 		"fhir_gateway.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "fhir_gateway.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "fhir_gateway.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "fhir_gateway.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "fhir_gateway.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["fhir_gateway.utils.before_request"]
# after_request = ["fhir_gateway.utils.after_request"]

# Job Events
# ----------
# before_job = ["fhir_gateway.utils.before_job"]
# after_job = ["fhir_gateway.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"fhir_gateway.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

