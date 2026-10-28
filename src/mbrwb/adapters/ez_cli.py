"""Adapter profiles for the EZ Tools command-line parsers (2026.10 version family).

Every flag below was found, with its help text, in the official .NET 10 build of that tool
(see ``catalogue/data/static_evidence.json``; ``tests/test_adapter_profiles.py`` enforces
it). The binaries were not executed on the build host, so profiles carry
``AdapterVerification.STATIC_STRINGS`` until help output is captured from an installed
executable (Tools > Verify this version), which upgrades that exact hash to
``VERIFIED_HELP``.

Deliberately not offered in collected-evidence workflows:
* ``--vss`` (live Volume Shadow Copy processing, needs administrator);
* ``--sync`` (downloads maps/batch files from GitHub at run time; changes dependencies
  outside the reviewed installation);
* ``--dt`` (custom timestamp format; the timeline mappings expect the tools' default
  format, so changing it would make timestamps unparseable or misread);
* live-registry modes (``-l`` in SBECmd, AppCompatCacheParser without ``-f``).
"""
from __future__ import annotations

import os
from pathlib import Path

from ..catalogue.catalogue import load_catalogue
from ..domain.enums import AdapterVerification, ErrorCategory
from ..domain.errors import Preflight
from ..evidence.artifacts import HIVE_TYPES
from .base import (
    AdapterProfile,
    InputRole,
    ManagedOutput,
    OptionKind as K,
    OptionSpec as O,
    OutputPattern as P,
    Request,
    ToolAdapter,
)

FAMILY_2026_10 = r"2026\.10\.\d+"
NOT_OFFERED_COMMON = ("--vss", "--sync", "--dt")


def _evidence(tool_id: str) -> str:
    rec = load_catalogue().static_evidence.get(tool_id)
    if not rec:
        return "No static evidence record found."
    return (f"Option names and help text read from {rec['main_member']} {rec.get('product_version')} "
            f"(SHA-256 {rec['main_member_sha256'][:16]}..., archive {rec['archive_name']} SHA-256 {rec['archive_sha256'][:16]}...) "
            f"by read-only inspection on 2026-10-28. The binary was not executed.")


DEBUG = O("debug", "--debug", K.SWITCH, "Debug messages", "Show debug information during processing", advanced=True)
TRACE = O("trace", "--trace", K.SWITCH, "Trace messages", "Show trace information during processing", advanced=True)
MP = O("mp", "--mp", K.SWITCH, "High-precision timestamps", "When true, display higher precision for timestamps (7 fractional digits). Recommended so timeline events keep source precision.", default=True)
QUIET = O("quiet", "-q", K.SWITCH, "Quiet console", "Do not dump full details about each file processed. Speeds up processing when using --json or --csv", default=True)
CSV_OUT = ManagedOutput("csv", "--csv", "csv", "CSV output")
CONSOLE_LOG = P("*_ConsoleLog.txt", "log", "console_log")


def _csv_pattern(glob: str, role: str, schema: str | None) -> P:
    return P(glob, "csv", role, schema, True)


def file_or_dir(file_types: tuple[str, ...], dir_types: tuple[str, ...], file_label="File", dir_label="Folder (recursive)") -> tuple[InputRole, InputRole]:
    return (
        InputRole("file", "-f", file_label, False, file_types, required=True, group="input", help="File to process. Either this or the folder is required"),
        InputRole("dir", "-d", dir_label, True, dir_types, required=True, group="input", help="Directory to recursively process. Either this or the file is required"),
    )


# -- PECmd -------------------------------------------------------------------------------
class PECmdAdapter(ToolAdapter):
    tool_id = "pecmd"
    profiles = (
        AdapterProfile(
            "pecmd.2026.10", "pecmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("pecmd"),
            inputs=file_or_dir(("prefetch_file",), ("prefetch_directory", "collection_root", "any_directory"), "Prefetch file", "Prefetch folder"),
            options=(
                QUIET, MP,
                O("json", "--json", K.SWITCH, "Also write JSON", "Directory to save JSON formatted results to (managed: job output/json)"),
                O("html", "--html", K.SWITCH, "Also write XHTML report", "Directory to save xhtml formatted results to (managed: job output/html)", advanced=True),
                O("keywords", "-k", K.TEXT, "Highlight keywords", "Comma separated list of keywords to highlight in output. By default, 'temp' and 'tmp' are highlighted. Any additional keywords will be added to these", advanced=True),
                O("dedupe", "--dedupe", K.SWITCH, "Deduplicate by SHA-1", "Deduplicate -f or -d & VSCs based on SHA-1. First file found wins", advanced=True),
                O("ads", "--ads", K.SWITCH, "Scan alternate data streams", "Scan alternate data streams of every file under -d (or the -f file) and parse any prefetch data found hidden in them", advanced=True),
                O("save_bytes", "-o", K.SWITCH, "Save decompressed Prefetch bytes", "When specified, save prefetch file bytes to the given path (managed: job output/prefetch_bytes). Useful to look at decompressed Win10 files", advanced=True),
                DEBUG, TRACE,
            ),
            managed_outputs=(
                CSV_OUT,
                ManagedOutput("json_out", "--json", "json", "JSON output", option="json"),
                ManagedOutput("html_out", "--html", "html", "XHTML output", option="html"),
                ManagedOutput("bytes_out", "-o", "prefetch_bytes", "Decompressed Prefetch bytes", option="save_bytes"),
            ),
            output_patterns=(
                _csv_pattern("*_PECmd_Output_Timeline.csv", "pecmd.timeline", "pecmd.timeline"),
                _csv_pattern("*_PECmd_Output.csv", "pecmd.files", "pecmd.files"),
                P("*_PECmd_Output*.json", "json", "pecmd.json", None, True),
                P("html/*", "html", "pecmd.html"),
                P("prefetch_bytes/*", "binary", "pecmd.bytes"),
                CONSOLE_LOG,
            ),
            not_offered=NOT_OFFERED_COMMON,
            notes=("Prefetch run times are execution evidence on systems with Prefetch enabled; absence of a .pf file does not prove a program never ran.",),
        ),
    )



# -- MFTECmd -----------------------------------------------------------------------------
MFT_INPUT_TYPES = ("ntfs_mft", "ntfs_usnjrnl_j", "ntfs_logfile", "ntfs_boot", "ntfs_sds", "ntfs_i30")


class MFTECmdAdapter(ToolAdapter):
    tool_id = "mftecmd"
    profiles = (
        AdapterProfile(
            "mftecmd.2026.10", "mftecmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("mftecmd"),
            inputs=(
                InputRole("file", "-f", "NTFS metadata file", False, MFT_INPUT_TYPES, required=True,
                          help="File to process ($MFT | $J | $LogFile | $Boot | $SDS | $I30). Required"),
                InputRole("mft", "-m", "Matching $MFT (same volume)", False, ("ntfs_mft",),
                          help="$MFT file to use when -f points to a $J or $LogFile file, to resolve parent paths. For $J, the paths are as they were when each record was written"),
            ),
            options=(
                O("csv", "--csv", K.SWITCH, "Write CSV", "Directory to save CSV formatted results to (managed). This or --json required unless --de or --body is specified", default=True),
                O("json", "--json", K.SWITCH, "Write JSON", "Directory to save JSON formatted results to (managed). This or --csv required unless --de or --body is specified"),
                O("body", "--body", K.SWITCH, "Write bodyfile", "Directory to save bodyfile formatted results to (managed). --bdl is also required when using this option", advanced=True),
                O("bdl", "--bdl", K.TEXT, "Bodyfile drive letter", "Drive letter (C, D, etc.) to use with bodyfile. Only the drive letter itself should be provided", advanced=True, pattern=r"[A-Za-z]", pattern_hint="a single drive letter such as C"),
                O("blf", "--blf", K.SWITCH, "Bodyfile LF newlines", "When true, use LF vs CRLF for newlines", advanced=True, depends_on=("body",)),
                O("sn", "--sn", K.SWITCH, "Include DOS names", "Include DOS file name types in $MFT output", advanced=True),
                O("fl", "--fl", K.SWITCH, "File listing", "Generate condensed file listing of parsed $MFT contents. Requires --csv", advanced=True, depends_on=("csv",)),
                O("at", "--at", K.SWITCH, "All $FN timestamps", "When true, include all timestamps from 0x30 attribute vs only when they differ from 0x10 in the $MFT", advanced=True),
                O("rs", "--rs", K.SWITCH, "Recover slack", "When true, recover slack space from FILE records when processing $MFT files. This option has no effect for $I30 files", advanced=True),
                O("ir", "--ir", K.SWITCH, "Include resident data", "Include resident data in JSON/CSV output", advanced=True),
                O("re", "--re", K.TEXT, "Resident data extensions", "Comma-separated list of extensions to include for resident data (e.g., '.txt,.ps1,.bat'). If omitted, includes all", advanced=True, depends_on=("ir",), pattern=r"(\.[A-Za-z0-9_]+)(,\s*\.[A-Za-z0-9_]+)*", pattern_hint="comma-separated extensions like .txt,.ps1"),
                O("rm", "--rm", K.INT, "Resident data max bytes", "Maximum size in bytes for resident data to include (max: 1024000)", advanced=True, minimum=1, maximum=1024000, depends_on=("ir",)),
                O("cutoff", "--cutoff", K.TEXT, "Cutoff date", "Only include $MFT records whose $STANDARD_INFO created or modified time is on or after this date/time (UTC unless an offset is given)", advanced=True,
                  pattern=r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?)?(Z|[+-]\d{2}:\d{2})?", pattern_hint="use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS, optionally with an offset"),
                O("dr", "--dr", K.SWITCH, "Dump resident files", "When true, dump $MFT resident files to dir specified by --csv or --json, in 'Resident' subdirectory", advanced=True),
                O("de", "--de", K.TEXT, "Dump entry details", "Dump full details for $MFT entry/sequence #. Format is 'Entry' or 'Entry-Seq' as decimal or hex. Example: 5, 624-5 or 0x270-0x5.", advanced=True,
                  pattern=r"(0x[0-9A-Fa-f]+|\d+)(-(0x[0-9A-Fa-f]+|\d+))?", pattern_hint="Entry or Entry-Seq, decimal or hex"),
                O("fls", "--fls", K.SWITCH, "List directory (with --de)", "When true, displays contents of directory from $MFT specified by --de. Ignored when --de points to a file", advanced=True, depends_on=("de",)),
                O("ds", "--ds", K.TEXT, "Dump security ID", "Dump full details for Security Id from $SDS as decimal or hex. Example: 624 or 0x270", advanced=True, pattern=r"(0x[0-9A-Fa-f]+|\d+)", pattern_hint="decimal or hex number"),
                O("dedupe", "--dedupe", K.SWITCH, "Deduplicate by SHA-1", "Deduplicate -f & VSCs based on SHA-1. First file found wins", advanced=True),
                O("lowmem", "--lowmem", K.SWITCH, "Low memory mode", "Use less memory at the cost of speed (workstation instead of server garbage collection): about 30% less peak memory, and about 1.4x slower", advanced=True),
                DEBUG, TRACE,
            ),
            managed_outputs=(
                ManagedOutput("csv_out", "--csv", "csv", "CSV output", option="csv"),
                ManagedOutput("json_out", "--json", "json", "JSON output", option="json"),
                ManagedOutput("body_out", "--body", "body", "Bodyfile output", option="body"),
            ),
            output_patterns=(
                _csv_pattern("*_MFTECmd_$MFT_Output.csv", "mftecmd.mft", "mftecmd.mft"),
                _csv_pattern("*$MFT_Output_FileListing*.csv", "mftecmd.filelisting", None),
                _csv_pattern("*_MFTECmd_$J_Output*.csv", "mftecmd.j", "mftecmd.j"),
                _csv_pattern("*_MFTECmd_$Boot_Output.csv", "mftecmd.boot", None),
                _csv_pattern("*_MFTECmd_$SDS_Output.csv", "mftecmd.sds", None),
                _csv_pattern("*_MFTECmd_$I30_Output.csv", "mftecmd.i30", None),
                _csv_pattern("*_MFTECmd_*_Output*.csv", "mftecmd.other", None),
                P("*_MFTECmd_*_Output*.json", "json", "mftecmd.json", None, True),
                P("*.body", "body", "mftecmd.body"),
                P("*Resident/*", "binary", "mftecmd.resident"),
                CONSOLE_LOG,
            ),
            not_offered=NOT_OFFERED_COMMON + ("--dd", "--do"),
            notes=("$MFT timestamps are file system metadata ($SI and $FN); they can be altered and do not by themselves show execution.",
                   "--dd/--do (export raw FILE records) are not offered in v1."),
        ),
    )

    def validate_specific(self, profile: AdapterProfile, request: Request, pf: Preflight) -> None:
        o = request.options
        f = (request.inputs.get("file") or [None])[0]
        m = (request.inputs.get("mft") or [None])[0]
        ftype = f.artifact_type if f else None
        if not (o.get("csv") or o.get("json") or o.get("body") or o.get("de") or o.get("ds")) and ftype != "ntfs_boot":
            pf.add(ErrorCategory.INPUT_MISMATCH, "Choose an output: CSV, JSON or bodyfile (or a dump detail option).",
                   "Tick 'Write CSV'.", field="csv")
        if o.get("body") and not o.get("bdl"):
            pf.add(ErrorCategory.INPUT_MISMATCH, "--bdl is required when using --body.", "Enter the drive letter for the bodyfile.", field="bdl")
        if m and ftype not in ("ntfs_usnjrnl_j", "ntfs_logfile", None):
            pf.add(ErrorCategory.INPUT_MISMATCH, "A matching $MFT (-m) is only used when the input is $J or $LogFile.",
                   "Clear the matching $MFT input.", field="mft")
        if ftype in ("ntfs_usnjrnl_j", "ntfs_logfile") and not m:
            pf.add(ErrorCategory.DEPENDENCY_MISSING, "No matching $MFT selected: parent paths in $J/$LogFile output will be unresolved.",
                   "Select the $MFT from the same volume if it was collected.", field="mft", blocking=False)
        if f and m and (f.volume or f.host) and (m.volume, m.host) != (f.volume, f.host):
            pf.add(ErrorCategory.INPUT_MISMATCH, "The selected $MFT is registered to a different host/volume than the input file.",
                   "Choose the $MFT from the same volume, or correct the evidence volume association.", field="mft")
        if o.get("ds") and ftype not in ("ntfs_sds", None):
            pf.add(ErrorCategory.INPUT_MISMATCH, "--ds applies to $SDS input only.", "Clear 'Dump security ID'.", field="ds")
        if o.get("de") and ftype not in ("ntfs_mft", None):
            pf.add(ErrorCategory.INPUT_MISMATCH, "--de applies to $MFT input only.", "Clear 'Dump entry details'.", field="de")



# -- EvtxECmd ----------------------------------------------------------------------------
EVENT_ID_LIST = r"\d+(-\d+)?(,\s*\d+(-\d+)?)*"
EZ_DATE = r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2}(\.\d{1,7})?)?"


class EvtxECmdAdapter(ToolAdapter):
    tool_id = "evtxecmd"
    profiles = (
        AdapterProfile(
            "evtxecmd.2026.10", "evtxecmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("evtxecmd"),
            inputs=file_or_dir(("evtx_file",), ("evtx_directory", "collection_root", "any_directory"), "Event log file", "Event logs folder"),
            options=(
                O("maps", "--maps", K.DEPENDENCY, "Event maps", "The path where event maps are located (managed: the 'Maps' folder of the selected installation, hashed into the job record)", default=True, dependency_glob="Maps"),
                O("json", "--json", K.SWITCH, "Also write JSON", "Directory to save JSON formatted results to (managed: job output/json)"),
                O("xml", "--xml", K.SWITCH, "Also write XML", "Directory to save XML formatted results to (managed: job output/xml)", advanced=True),
                O("fj", "--fj", K.SWITCH, "Full JSON", "When true, export all available data when using --json", advanced=True, depends_on=("json",)),
                O("inc", "--inc", K.TEXT, "Include event IDs", "List of Event IDs to process. All others are ignored. Overrides --exc Format is 4624,4625,5410,5500-5600", pattern=EVENT_ID_LIST, pattern_hint="comma-separated IDs or ranges, e.g. 4624,4625,5500-5600", conflicts=("exc",)),
                O("exc", "--exc", K.TEXT, "Exclude event IDs", "List of Event IDs to IGNORE. All others are included. Format is 4624,4625,5410,5500-5600", advanced=True, pattern=EVENT_ID_LIST, pattern_hint="comma-separated IDs or ranges"),
                O("sd", "--sd", K.TEXT, "Start date (UTC)", "Start date for including events (UTC). Anything OLDER than this is dropped. Format should match --dt", advanced=True, pattern=EZ_DATE, pattern_hint="YYYY-MM-DD HH:MM:SS (UTC)"),
                O("ed", "--ed", K.TEXT, "End date (UTC)", "End date for including events (UTC). Anything NEWER than this is dropped. Format should match --dt", advanced=True, pattern=EZ_DATE, pattern_hint="YYYY-MM-DD HH:MM:SS (UTC)"),
                O("tdt", "--tdt", K.INT, "Time discrepancy seconds", "The number of seconds to use for time discrepancy detection. Default is 1", advanced=True, minimum=0, maximum=86400),
                O("met", "--met", K.BOOL, "Show metrics", "When true, show metrics about processed event log. Default is true", advanced=True, choices=("true", "false")),
                O("dedupe", "--dedupe", K.SWITCH, "Deduplicate by SHA-1", "Deduplicate -f or -d & VSCs based on SHA-1. First file found wins", advanced=True),
                DEBUG, TRACE,
            ),
            managed_outputs=(
                CSV_OUT,
                ManagedOutput("json_out", "--json", "json", "JSON output", option="json"),
                ManagedOutput("xml_out", "--xml", "xml", "XML output", option="xml"),
            ),
            output_patterns=(
                _csv_pattern("*_EvtxECmd_Output.csv", "evtxecmd.events", "evtxecmd.events"),
                P("*_EvtxECmd_Output.json", "json", "evtxecmd.json", None, True),
                P("*_EvtxECmd_Output.xml", "xml", "evtxecmd.xml"),
                CONSOLE_LOG,
            ),
            not_offered=NOT_OFFERED_COMMON,
            notes=("Maps add interpretation columns (MapDescription, PayloadData*); the raw event fields remain the source of truth.",),
        ),
    )



# -- AmcacheParser -----------------------------------------------------------------------
class AmcacheParserAdapter(ToolAdapter):
    tool_id = "amcacheparser"
    profiles = (
        AdapterProfile(
            "amcacheparser.2026.10", "amcacheparser", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("amcacheparser"),
            inputs=(
                InputRole("file", "-f", "Amcache.hve, its folder, or an Inventory*.db", False, ("amcache_hive", "sqlite_db", "registry_hive"), required=True, group="input",
                          help="Amcache.hve to parse (Windows 7 to 11), the folder it's in (C:\\Windows\\AppCompat\\Programs), or one of the Inventory*.db SQLite databases there"),
                InputRole("folder", "-f", "Amcache folder (Programs)", True, ("amcache_folder", "any_directory"), required=True, group="input",
                          help="The folder Amcache.hve is in (C:\\Windows\\AppCompat\\Programs); also processes the Inventory*.db databases"),
            ),
            options=(
                O("include_files", "-i", K.SWITCH, "Include file entries for programs", "Include file entries for Programs entries", default=True),
                O("exclude_list", "-w", K.FILE, "SHA-1 exclude list", "Path to file containing SHA-1 hashes to *exclude* from the results. Blacklisting overrides whitelisting", advanced=True),
                O("include_list", "-b", K.FILE, "SHA-1 include list", "Path to file containing SHA-1 hashes to *include* from the results. Blacklisting overrides whitelisting", advanced=True),
                MP,
                O("nl", "--nl", K.BOOL, "Ignore transaction logs", "When true, ignore transaction log files for dirty hives. Default is FALSE", advanced=True, choices=("true", "false")),
                DEBUG, TRACE,
            ),
            managed_outputs=(CSV_OUT, ManagedOutput("temp", "--temp", "../temp/amcache", "Temporary database copies")),
            output_patterns=(
                _csv_pattern("*_UnassociatedFileEntries.csv", "amcache.unassociated", "amcache.unassociated"),
                _csv_pattern("*_AssociatedFileEntries.csv", "amcache.associated", "amcache.associated"),
                _csv_pattern("*_ProgramEntries.csv", "amcache.programs", "amcache.programs"),
                _csv_pattern("*_ShortCuts.csv", "amcache.shortcuts", None),
                _csv_pattern("*_DriverBinaries.csv", "amcache.drivers", None),
                _csv_pattern("*_DriverPackages.csv", "amcache.driverpackages", None),
                _csv_pattern("*_DevicePnps.csv", "amcache.devicepnps", None),
                _csv_pattern("*_DeviceContainers.csv", "amcache.devicecontainers", None),
                _csv_pattern("*.csv", "amcache.other", None),
                CONSOLE_LOG,
            ),
            not_offered=NOT_OFFERED_COMMON,
            notes=("Amcache records presence/inventory of files and programs. An entry is not proof that a program executed.",),
        ),
    )


# -- AppCompatCacheParser ----------------------------------------------------------------
class AppCompatCacheParserAdapter(ToolAdapter):
    tool_id = "appcompatcacheparser"
    profiles = (
        AdapterProfile(
            "appcompatcacheparser.2026.10", "appcompatcacheparser", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("appcompatcacheparser"),
            inputs=(InputRole("file", "-f", "SYSTEM hive", False, ("system_hive", "registry_hive"), required=True,
                              help="Full path to SYSTEM hive to process. (Without it the tool reads the live Registry; the workbench never omits it.)"),),
            options=(
                O("controlset", "-c", K.INT, "Control set", "The ControlSet to parse. Default is to extract all control sets", advanced=True, minimum=0, maximum=999),
                O("sort", "-t", K.SWITCH, "Sort timestamps descending", "Sorts last modified timestamps in descending order", advanced=True),
                O("nl", "--nl", K.BOOL, "Ignore transaction logs", "When true, ignore transaction log files for dirty hives", advanced=True, choices=("true", "false")),
                DEBUG, TRACE,
            ),
            managed_outputs=(CSV_OUT,),
            output_patterns=(_csv_pattern("*_AppCompatCache.csv", "appcompatcache.entries", "appcompatcache.entries"), _csv_pattern("*.csv", "appcompatcache.other", None), CONSOLE_LOG),
            not_offered=NOT_OFFERED_COMMON,
            notes=("ShimCache shows files the OS evaluated for compatibility; the timestamp is the file's last-modified time, not an execution time. Execution semantics differ by Windows version.",),
        ),
    )

    def validate_specific(self, profile, request, pf):
        f = (request.inputs.get("file") or [None])[0]
        if f and f.artifact_type == "registry_hive":
            pf.add(ErrorCategory.INPUT_MISMATCH, "The selected hive is not identified as SYSTEM.", "Confirm it is the SYSTEM hive on the Evidence page.", field="file", blocking=False)


# -- RECmd -------------------------------------------------------------------------------
class RECmdAdapter(ToolAdapter):
    tool_id = "recmd"
    profiles = (
        AdapterProfile(
            "recmd.2026.10", "recmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("recmd"),
            inputs=file_or_dir(HIVE_TYPES, ("registry_hive_directory", "collection_root", "any_directory"), "Hive", "Folder with hives (recursive)"),
            options=(
                O("batch", "--bn", K.DEPENDENCY, "Batch file", "Use settings from supplied batch file to find keys/values (from the installation's BatchExamples folder; hashed into the job record)", default="BatchExamples/Kroll_Batch.reb", dependency_glob="BatchExamples"),
                O("json", "--json", K.SWITCH, "Also write JSON", "Directory to save JSON formatted results to (managed: job output/json)"),
                O("nl", "--nl", K.BOOL, "Allow missing transaction logs", "When true, allow transaction log files to not exist for dirty hives", advanced=True, choices=("true", "false")),
                O("recover", "--recover", K.BOOL, "Recover deleted keys/values", "If true, recover deleted keys/values. Default is true", advanced=True, choices=("true", "false")),
                O("dedupe", "--dedupe", K.SWITCH, "Deduplicate by SHA-1", "Deduplicate -f or -d & VSCs based on SHA-1. First file found wins", advanced=True),
                O("details", "--details", K.SWITCH, "More details", "Show more details when displaying results", advanced=True),
                DEBUG, TRACE,
            ),
            managed_outputs=(CSV_OUT, ManagedOutput("json_out", "--json", "json", "JSON output", option="json")),
            output_patterns=(_csv_pattern("*_RECmd_Batch_*_Output.csv", "recmd.batch", "recmd.batch"), _csv_pattern("*.csv", "recmd.other", None),
                             P("*.json", "json", "recmd.json", None, True), CONSOLE_LOG),
            not_offered=NOT_OFFERED_COMMON + ("--sa", "--sk", "--sv", "--sd", "--ss", "--kn", "--vn", "--saveTo", "--base64", "--minSize", "--regex", "--literal", "--nd"),
            notes=("Batch mode is the supported workflow. Interactive search switches (--sk/--sv/--sd/--ss/--kn/...) print to the console and are left to Registry Explorer or a later adapter version.",
                   "Plugins are loaded by RECmd from its own installation folder."),
        ),
    )

    def resolve_dependencies(self, inst, profile, options):
        resolved, snapshot, pf = super().resolve_dependencies(inst, profile, options)
        if "batch" in resolved and not resolved["batch"].lower().endswith(".reb"):
            pf.add(ErrorCategory.DEPENDENCY_MISSING, "The batch file must be a RECmd batch (.reb) file.", "Choose a .reb file from BatchExamples.", field="batch")
        plugins = Path(inst.install_dir) / "Plugins"
        if plugins.is_dir():
            from .base import inventory
            snapshot["plugins"] = inventory(plugins)
        return resolved, snapshot, pf

    def validate_specific(self, profile, request, pf):
        if not request.options.get("batch"):
            pf.add(ErrorCategory.DEPENDENCY_MISSING, "Select a batch file.", "Choose a .reb file from the installation's BatchExamples folder.", field="batch")



# -- RLA ---------------------------------------------------------------------------------
class RLAAdapter(ToolAdapter):
    tool_id = "rla"
    profiles = (
        AdapterProfile(
            "rla.2026.10", "rla", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("rla"),
            inputs=file_or_dir(HIVE_TYPES, ("registry_hive_directory", "collection_root", "any_directory"), "Hive (with its .LOG1/.LOG2 in the same folder)", "Folder with hives"),
            options=(
                O("ca", "--ca", K.BOOL, "Always copy hives", "When true, always copy hives to --out directory, even if they aren't dirty", choices=("true", "false"), default="true"),
                O("nop", "--nop", K.BOOL, "Flat output", "When true, save hives directly in --out. When false, save them under their original path in --out", advanced=True, choices=("true", "false")),
                O("cn", "--cn", K.BOOL, "Unique profile hive names", "When true and --nop is true, name profile based hives (NTUSER.DAT, UsrClass.dat) drive_profile_name, so they don't collide", advanced=True, choices=("true", "false")),
                DEBUG, TRACE,
            ),
            managed_outputs=(ManagedOutput("out", "--out", "derived_hives", "Replayed (derived) hives"),),
            output_patterns=(CONSOLE_LOG, P("derived_hives/*.csv", "csv", "rla.report", None, False), P("derived_hives/*.txt", "text", "rla.report"), P("derived_hives/*", "hive", "rla.derived_hive")),
            not_offered=NOT_OFFERED_COMMON,
            notes=("RLA writes replayed copies only into the job's derived_hives folder. The original hive and transaction logs are never written; derived hives are registered as new evidence with parent links.",),
        ),
    )

    def validate_specific(self, profile, request, pf):
        f = (request.inputs.get("file") or [None])[0]
        if f:
            p = Path(f.path)
            logs = [p.with_name(p.name + s) for s in (".LOG1", ".LOG2", ".LOG")]
            if not any(x.exists() for x in logs):
                pf.add(ErrorCategory.DEPENDENCY_MISSING, "No transaction logs (.LOG1/.LOG2) were found next to this hive.",
                       "Register the logs in the same folder; RLA skips dirty hives without logs unless 'Always copy hives' is true.", field="file", blocking=False)


# -- JLECmd / LECmd ----------------------------------------------------------------------
class JLECmdAdapter(ToolAdapter):
    tool_id = "jlecmd"
    profiles = (
        AdapterProfile(
            "jlecmd.2026.10", "jlecmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("jlecmd"),
            inputs=file_or_dir(("jumplist_file",), ("jumplist_directory", "collection_root", "any_directory"), "Jump List file", "Jump Lists folder"),
            options=(
                QUIET, MP,
                O("user", "--user", K.TEXT, "User name to record", "Username to include in JSON/CSV output (e.g. the owner of the jump list artifacts). Defaults to the evidence's user profile."),
                O("json", "--json", K.SWITCH, "Also write JSON", "Directory to save JSON formatted results to (managed)", advanced=True),
                O("html", "--html", K.SWITCH, "Also write XHTML", "Directory to save xhtml formatted results to (managed)", advanced=True),
                O("pretty", "--pretty", K.SWITCH, "Pretty JSON", "When exporting to json, use a more human readable layout", advanced=True, depends_on=("json",)),
                O("all", "--all", K.SWITCH, "Process all files", "Process all files in directory vs. only files matching *.automaticDestinations-ms or *.customDestinations-ms", advanced=True),
                O("ld", "--ld", K.SWITCH, "More lnk detail", "Include more information about lnk files", advanced=True),
                O("fd", "--fd", K.SWITCH, "Full lnk detail", "Include full information about lnk files", advanced=True),
                O("appids", "--appIds", K.FILE, "AppID list file", "Path to file containing AppIDs and descriptions (appid|description format)", advanced=True),
                O("cp", "--cp", K.INT, "Code page", "Code page to parse strings", advanced=True, minimum=1, maximum=65535),
                DEBUG, TRACE,
            ),
            managed_outputs=(CSV_OUT, ManagedOutput("json_out", "--json", "json", "JSON output", option="json"), ManagedOutput("html_out", "--html", "html", "XHTML output", option="html")),
            output_patterns=(_csv_pattern("*_AutomaticDestinations.csv", "jlecmd.automatic", "jlecmd.automatic"), _csv_pattern("*_CustomDestinations.csv", "jlecmd.custom", "jlecmd.custom"),
                             _csv_pattern("*.csv", "jlecmd.other", None), P("json/*.json", "json", "jlecmd.json", None, True), P("html/*", "html", "jlecmd.html"), CONSOLE_LOG),
            not_offered=NOT_OFFERED_COMMON + ("--dumpTo",),
        ),
    )



class LECmdAdapter(ToolAdapter):
    tool_id = "lecmd"
    profiles = (
        AdapterProfile(
            "lecmd.2026.10", "lecmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("lecmd"),
            inputs=file_or_dir(("lnk_file",), ("lnk_directory", "collection_root", "any_directory"), "Shortcut (.lnk)", "Shortcuts folder"),
            options=(
                QUIET, MP,
                O("json", "--json", K.SWITCH, "Also write JSON", "Directory to save JSON formatted results to (managed)", advanced=True),
                O("xml", "--xml", K.SWITCH, "Also write XML", "Directory to save XML formatted results to (managed)", advanced=True),
                O("html", "--html", K.SWITCH, "Also write XHTML", "Directory to save xhtml formatted results to (managed)", advanced=True),
                O("pretty", "--pretty", K.SWITCH, "Pretty JSON", "When exporting to json, use a more human readable layout", advanced=True, depends_on=("json",)),
                O("removable", "-r", K.SWITCH, "Removable targets only", "Only process lnk files pointing to removable drives", advanced=True),
                O("all", "--all", K.SWITCH, "Process all files", "Process all files in directory vs. only files matching *.lnk", advanced=True),
                O("nid", "--nid", K.SWITCH, "Suppress target ID list", "Suppress Target ID list details from being displayed", advanced=True),
                O("neb", "--neb", K.SWITCH, "Suppress extra blocks", "Suppress Extra blocks information from being displayed", advanced=True),
                O("cp", "--cp", K.INT, "Code page", "Code page to parse strings", advanced=True, minimum=1, maximum=65535),
                DEBUG, TRACE,
            ),
            managed_outputs=(CSV_OUT, ManagedOutput("json_out", "--json", "json", "JSON output", option="json"), ManagedOutput("xml_out", "--xml", "xml", "XML output", option="xml"),
                             ManagedOutput("html_out", "--html", "html", "XHTML output", option="html")),
            output_patterns=(_csv_pattern("*_LECmd_Output.csv", "lecmd.lnk", "lecmd.lnk"), _csv_pattern("*.csv", "lecmd.other", None), P("json/*", "json", "lecmd.json", None, True),
                             P("xml/*", "xml", "lecmd.xml"), P("html/*", "html", "lecmd.html"), CONSOLE_LOG),
            not_offered=NOT_OFFERED_COMMON,
        ),
    )



# -- SBECmd ------------------------------------------------------------------------------
class SBECmdAdapter(ToolAdapter):
    tool_id = "sbecmd"
    profiles = (
        AdapterProfile(
            "sbecmd.2026.10", "sbecmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("sbecmd"),
            inputs=(InputRole("dir", "-d", "Folder with user hives", True, ("registry_hive_directory", "collection_root", "any_directory"), required=True,
                              help="Directory to look for registry hives (NTUSER.DAT / UsrClass.dat)"),),
            options=(
                O("json", "--json", K.SWITCH, "Also write JSON", "Directory to save JSON formatted results to (managed)", advanced=True),
                O("dedupe", "--dedupe", K.BOOL, "Deduplicate across hives", "When true, SBECmd processes all hives in -d <directory> and removes duplicates", advanced=True, choices=("true", "false")),
                O("nl", "--nl", K.BOOL, "Allow missing transaction logs", "When true, allow transaction log files to not exist for dirty hives", advanced=True, choices=("true", "false")),
                DEBUG, TRACE,
            ),
            managed_outputs=(CSV_OUT, ManagedOutput("json_out", "--json", "json", "JSON output", option="json")),
            output_patterns=(_csv_pattern("*.csv", "sbecmd.shellbags", "sbecmd.shellbags"), P("json/*", "json", "sbecmd.json", None, True), CONSOLE_LOG),
            not_offered=NOT_OFFERED_COMMON + ("-l",),
            notes=("ShellBags show folders the shell displayed/stored state for; a ShellBag timestamp is not by itself proof that the user opened that folder at that time.",),
        ),
    )



# -- SrumECmd ----------------------------------------------------------------------------
class SrumECmdAdapter(ToolAdapter):
    tool_id = "srumecmd"
    profiles = (
        AdapterProfile(
            "srumecmd.2026.10", "srumecmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("srumecmd"),
            inputs=(
                InputRole("file", "-f", "SRUDB.dat", False, ("srum_db",), required=True, group="input", help="SRUDB.dat file to parse"),
                InputRole("software", "-r", "Matching SOFTWARE hive (optional)", False, ("software_hive", "registry_hive"), help="SOFTWARE hive to process. This is optional, but recommended"),
                InputRole("dir", "-d", "Folder with SRUDB.dat and SOFTWARE", True, ("collection_root", "any_directory"), required=True, group="input",
                          help="Directory to recursively process, looking for SRUDB.dat and SOFTWARE hive"),
            ),
            options=(DEBUG, TRACE),
            managed_outputs=(CSV_OUT, ManagedOutput("temp", "--temp", "../temp/srum", "Temporary database copies")),
            output_patterns=(_csv_pattern("*SrumECmd_*_Output.csv", "srum.table", "srum"), _csv_pattern("*.csv", "srum.other", None), CONSOLE_LOG),
            not_offered=NOT_OFFERED_COMMON,
            notes=("SRUM records resource usage per application and interface. Network byte counts alone do not prove data exfiltration.",),
        ),
    )

    def validate_specific(self, profile, request, pf):
        f = (request.inputs.get("file") or [None])[0]
        r = (request.inputs.get("software") or [None])[0]
        if request.inputs.get("dir") and r:
            pf.add(ErrorCategory.INPUT_MISMATCH, "The SOFTWARE hive input (-r) is used with a SRUDB.dat file, not with folder mode.", "Clear one of them.", field="software")
        if f and not r:
            pf.add(ErrorCategory.DEPENDENCY_MISSING, "No matching SOFTWARE hive selected; application and network names may stay unresolved.",
                   "Select the SOFTWARE hive from the same host if collected.", field="software", blocking=False)
        if f and r and (f.host or r.host) and f.host != r.host:
            pf.add(ErrorCategory.INPUT_MISMATCH, "SRUDB.dat and the SOFTWARE hive are registered to different hosts.",
                   "Select the SOFTWARE hive from the same host.", field="software")


# -- SQLECmd -----------------------------------------------------------------------------
class SQLECmdAdapter(ToolAdapter):
    tool_id = "sqlecmd"
    profiles = (
        AdapterProfile(
            "sqlecmd.2026.10", "sqlecmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("sqlecmd"),
            inputs=file_or_dir(("sqlite_db", "activitiescache_db"), ("sqlite_directory", "collection_root", "any_directory"), "SQLite database", "Folder (recursive)"),
            options=(
                O("maps", "--maps", K.DEPENDENCY, "SQL maps", "The path where SQL maps are located (managed: the installation's Maps folder, hashed into the job record)", default=True, dependency_glob="Maps"),
                O("json", "--json", K.SWITCH, "Also write JSON", "Directory to save JSON formatted results to (managed)", advanced=True),
                O("hunt", "--hunt", K.BOOL, "Hunt by file header", "When true, all files are looked at regardless of name and file header is used to identify SQLite files, else filename in map is used to find databases", advanced=True, choices=("true", "false")),
                O("noblob", "--noblob", K.BOOL, "Disable blob extraction", "If true, disables blob extraction from query results", advanced=True, choices=("true", "false"), default="true"),
                O("ifn", "--ifn", K.BOOL, "Database name in outputs", "If true, the name of each database is added to the names of its CSV and blob files, so databases processed with the same map can be told apart", advanced=True, choices=("true", "false"), default="true"),
                O("dedupe", "--dedupe", K.SWITCH, "Deduplicate by SHA-1", "Deduplicate -f or -d files based on SHA-1. First file found wins", advanced=True),
                O("debug", "--debug", K.SWITCH, "Debug messages", "Show debug information during processing", advanced=True),
                TRACE,
            ),
            managed_outputs=(CSV_OUT, ManagedOutput("json_out", "--json", "json", "JSON output", option="json"), ManagedOutput("temp", "--temp", "../temp/sqlite", "Temporary database copies")),
            output_patterns=(_csv_pattern("*.csv", "sqlecmd.map_output", None), P("json/*", "json", "sqlecmd.json", None, True), CONSOLE_LOG),
            not_offered=NOT_OFFERED_COMMON + ("--blobdir",),
            notes=("SQLECmd opens temporary copies of databases (with their -wal/-journal sidecars) in the job's temp folder, not the evidence.",
                   "Each map produces its own CSV; columns depend on the map, so no generic timeline mapping is applied."),
        ),
    )



# -- smaller parsers ---------------------------------------------------------------------
class RBCmdAdapter(ToolAdapter):
    tool_id = "rbcmd"
    profiles = (
        AdapterProfile(
            "rbcmd.2026.10", "rbcmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("rbcmd"),
            inputs=file_or_dir(("recyclebin_i_file", "recyclebin_info2"), ("recyclebin_directory", "collection_root", "any_directory"), "$I or INFO2 file", "Recycle Bin folder"),
            options=(O("quiet", "-q", K.SWITCH, "Quiet console", "Only show the filename being processed vs all output. Useful to speed up exporting to JSON and/or CSV", default=True), DEBUG, TRACE),
            managed_outputs=(CSV_OUT,),
            output_patterns=(_csv_pattern("*_RBCmd_Output.csv", "rbcmd.entries", "rbcmd.entries"), CONSOLE_LOG),
            not_offered=NOT_OFFERED_COMMON,
        ),
    )


class RecentFileCacheParserAdapter(ToolAdapter):
    tool_id = "recentfilecacheparser"
    profiles = (
        AdapterProfile(
            "recentfilecacheparser.2026.10", "recentfilecacheparser", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("recentfilecacheparser"),
            inputs=(InputRole("file", "-f", "RecentFileCache.bcf", False, ("recentfilecache_bcf",), required=True, help="File to process. Required"),),
            options=(
                O("quiet", "-q", K.SWITCH, "Quiet console", "Only show the filename being processed vs all output", default=True),
                O("json", "--json", K.SWITCH, "Also write JSON", "Directory to save json representation to (managed)", advanced=True),
                O("pretty", "--pretty", K.SWITCH, "Pretty JSON", "When exporting to json, use a more human readable layout", advanced=True, depends_on=("json",)),
            ),
            managed_outputs=(CSV_OUT, ManagedOutput("json_out", "--json", "json", "JSON output", option="json")),
            output_patterns=(_csv_pattern("*_RecentFileCacheParser_Output.csv", "rfc.entries", "rfc.entries"), P("json/*", "json", "rfc.json", None, True), CONSOLE_LOG),
            not_offered=("--dt",),
            notes=("The 2026.10 build exposes no --debug/--trace options.",),
        ),
    )



class SumECmdAdapter(ToolAdapter):
    tool_id = "sumecmd"
    profiles = (
        AdapterProfile(
            "sumecmd.2026.10", "sumecmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("sumecmd"),
            inputs=(InputRole("dir", "-d", "SUM folder", True, ("sum_directory", "collection_root", "any_directory"), required=True,
                              help="Directory to process, looking for SystemIdentity.mdb, Current.mdb, etc. Required."),),
            options=(O("wd", "--wd", K.BOOL, "Day-level detail CSV", "Generate CSV with day level details. Default is TRUE", choices=("true", "false")), DEBUG, TRACE),
            managed_outputs=(CSV_OUT, ManagedOutput("temp", "--temp", "../temp/sum", "Temporary database copies")),
            output_patterns=(_csv_pattern("*_Output.csv", "sum.table", None), _csv_pattern("*.csv", "sum.other", None), CONSOLE_LOG),
            not_offered=NOT_OFFERED_COMMON,
            notes=("Requires SystemIdentity.mdb in the folder to resolve role and host details; the workbench warns when it is not present.",),
        ),
    )

    def validate_specific(self, profile, request, pf):
        d = (request.inputs.get("dir") or [None])[0]
        if d and os.path.isdir(d.path):
            names = {n.lower() for n in os.listdir(d.path)}
            if "systemidentity.mdb" not in names:
                pf.add(ErrorCategory.DEPENDENCY_MISSING, "SystemIdentity.mdb was not found in the selected folder.",
                       "Select the complete SUM folder from the same server.", field="dir", blocking=False)
            if not any(n.endswith(".mdb") for n in names):
                pf.add(ErrorCategory.INPUT_MISMATCH, "No .mdb databases found in the selected folder.", "Select the SUM folder.", field="dir")


class WxTCmdAdapter(ToolAdapter):
    tool_id = "wxtcmd"
    profiles = (
        AdapterProfile(
            "wxtcmd.2026.10", "wxtcmd", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("wxtcmd"),
            inputs=(InputRole("file", "-f", "ActivitiesCache.db", False, ("activitiescache_db", "sqlite_db"), required=True, help="File to process"),),
            options=(DEBUG, TRACE),
            managed_outputs=(CSV_OUT, ManagedOutput("temp", "--temp", "../temp/wxt", "Temporary database copies")),
            output_patterns=(_csv_pattern("*_ActivityOperations.csv", "wxtcmd.operations", "wxtcmd.operations"), _csv_pattern("*_Activity_PackageIDs.csv", "wxtcmd.packageids", None),
                             _csv_pattern("*_Activity.csv", "wxtcmd.activity", "wxtcmd.activity"), CONSOLE_LOG),
            not_offered=("--dt",),
        ),
    )


class BstringsAdapter(ToolAdapter):
    tool_id = "bstrings"
    profiles = (
        AdapterProfile(
            "bstrings.2026.10", "bstrings", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("bstrings"),
            inputs=file_or_dir(("*",), ("*",), "File to search", "Folder to search (recursive)"),
            options=(
                O("really_quiet", "-s", K.SWITCH, "Do not echo hits to console", "Really Quiet mode (Do not display hits to console. Speeds up processing when using -o)", default=True),
                O("min", "-m", K.INT, "Minimum length", "Minimum string length", minimum=1, maximum=100000),
                O("max", "-x", K.INT, "Maximum length", "Maximum string length. Default is unlimited", advanced=True, minimum=1, maximum=1000000),
                O("ascii", "-a", K.BOOL, "ASCII strings", "If set, look for ASCII strings. Use -a false to disable", advanced=True, choices=("true", "false")),
                O("unicode", "-u", K.BOOL, "Unicode strings", "If set, look for Unicode strings. Use -u false to disable", advanced=True, choices=("true", "false")),
                O("ls", "--ls", K.TEXT, "Look for string", "String to look for. When set, only matching strings are returned", conflicts=("lr", "fs", "fr")),
                O("lr", "--lr", K.TEXT, "Look for regex / built-in pattern", "Regex to look for. When set, only strings matching the regex are returned", conflicts=("fs", "fr")),
                O("fs", "--fs", K.FILE, "Strings list file", "File containing strings to look for. When set, only matching strings are returned", advanced=True),
                O("fr", "--fr", K.FILE, "Regex list file", "File containing regex patterns to look for. When set, only strings matching regex patterns are returned", advanced=True),
                O("chunk", "-b", K.INT, "Chunk size MB", "Chunk size in MB. Valid range is 1 to 1024", advanced=True, minimum=1, maximum=1024),
                O("cp", "--cp", K.INT, "Code page", "Code page to use. Default is 1252", advanced=True, minimum=1, maximum=65535),
                O("mask", "--mask", K.TEXT, "File mask (folder mode)", "When using -d, file mask to search for. * and ? are supported. This option has no effect when using -f", advanced=True, pattern=r"[^\\/:\"<>|]+", pattern_hint="a file name mask such as *.dll"),
                O("ms", "--ms", K.INT, "Max file size (folder mode)", "When using -d, maximum file size in bytes to process", advanced=True, minimum=1),
                O("ro", "--ro", K.SWITCH, "Return regex match only", "When true, list the string matched by regex pattern vs string the pattern was found in", advanced=True),
                O("off", "--off", K.SWITCH, "Show offsets", "Show offset to hit after string, followed by the encoding (A=Code page, U=Unicode)", default=True),
                O("sa", "--sa", K.SWITCH, "Sort alphabetically", "Sort results alphabetically", advanced=True, conflicts=("sl",)),
                O("sl", "--sl", K.SWITCH, "Sort by length", "Sort results by length", advanced=True),
                O("quiet", "-q", K.SWITCH, "No header/totals", "Quiet mode (Do not show header or total number of hits)", advanced=True),
                DEBUG, TRACE,
            ),
            managed_outputs=(ManagedOutput("out", "-o", "", "Results file", kind="file", file_name="bstrings_results.txt"),),
            output_patterns=(P("bstrings_results.txt", "text", "bstrings.results"), CONSOLE_LOG),
            not_offered=("-p", "--ar", "--ur"),
            notes=("Results are text and open in the bounded text viewer; they are not imported as a table.",
                   "--ar/--ur character ranges are not offered in v1 (their quoting rules need real-tool verification)."),
        ),
    )

    def classify_completion(self, profile, result, outputs):
        c = super().classify_completion(profile, result, outputs)
        # bstrings output is text, never imported as a dataset
        return c


class IisGeolocateAdapter(ToolAdapter):
    tool_id = "iisgeolocate"
    profiles = (
        AdapterProfile(
            "iisgeolocate.2026.10", "iisgeolocate", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("iisgeolocate"),
            inputs=(InputRole("dir", "-d", "IIS logs folder", True, ("iis_log_directory", "collection_root", "any_directory"), required=True,
                              help="Directory to recursively process for IIS logs. This will be searched for *.log files"),),
            options=(
                O("db", "--db", K.FILE, "MaxMind database (.mmdb)", "MaxMind database to use (City, Enterprise or Country). Operator supplied; its name and date are recorded with the job"),
                O("sbl", "--sbl", K.BOOL, "Hide bad lines on console", "When true, do NOT show bad lines to console (they are still logged to a file). Default is false", advanced=True, choices=("true", "false"), default="true"),
                O("nul", "--nul", K.BOOL, "Skip updated CSV copies", "When true, do NOT create updated CSV files in --csv directory. Default is false", advanced=True, choices=("true", "false")),
            ),
            managed_outputs=(CSV_OUT,),
            output_patterns=(_csv_pattern("*UniqueIPs.csv", "iisgeolocate.uniqueips", None), _csv_pattern("*.csv", "iisgeolocate.logs", None),
                             P("*BadDataRows*", "text", "iisgeolocate.baddata"), CONSOLE_LOG),
            not_offered=(),
            notes=("Geolocation is optional enrichment from the local MaxMind database: record its source and date. The workbench does not download it.",),
        ),
    )

    def validate_specific(self, profile, request, pf):
        db = request.options.get("db")
        if not db:
            pf.add(ErrorCategory.DEPENDENCY_MISSING, "No MaxMind database selected.",
                   "Choose a GeoIP2/GeoLite2 .mmdb file; without one the tool looks next to its executable and may find none.", field="db", blocking=False)
        elif not str(db).lower().endswith(".mmdb"):
            pf.add(ErrorCategory.DEPENDENCY_MISSING, "The geolocation database should be a MaxMind .mmdb file.", "Choose the .mmdb file.", field="db")


class VSCMountAdapter(ToolAdapter):
    """Specialist live operation. Never part of a workflow preset."""

    tool_id = "vscmount"
    profiles = (
        AdapterProfile(
            "vscmount.2026.10", "vscmount", FAMILY_2026_10, AdapterVerification.STATIC_STRINGS, _evidence("vscmount"),
            inputs=(),
            options=(
                O("dl", "--dl", K.TEXT, "Source drive", "Source drive to look for Volume Shadow Copies (C, D:, or F:\\ for example)", pattern=r"[A-Za-z]:?\\?", pattern_hint="a drive such as C or C:"),
                O("ud", "--ud", K.BOOL, "Timestamp link names", "Use VSC creation timestamps (yyyyMMddTHHmmss.fffffff, UTC) in symbolic link names. Default is true", choices=("true", "false")),
                DEBUG, TRACE,
            ),
            managed_outputs=(ManagedOutput("mp", "--mp", "vsc_mounts", "Mount point base (owned by this job)"),),
            output_patterns=(CONSOLE_LOG,),
            console_only=True,
            exclusive=True,
            requires_elevation=True,
            notes=("Creates symbolic links to shadow copies under the job's vsc_mounts folder. Cleanup removes only links this job created; shadow copies are never deleted.",),
        ),
    )

    def validate_specific(self, profile, request, pf):
        if not request.options.get("dl"):
            pf.add(ErrorCategory.INPUT_MISMATCH, "Select the source drive.", "Enter a drive letter such as C.", field="dl")
