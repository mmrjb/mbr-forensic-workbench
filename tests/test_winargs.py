from __future__ import annotations

import subprocess

import pytest

from mbrwb.adapters.winargs import display_command, split_windows_args


@pytest.mark.parametrize(
    "text,expected",
    [
        ('a b c', ["a", "b", "c"]),
        ('"a b" c', ["a b", "c"]),
        (r'a\\b', [r"a\\b"]),
        (r'"a\\" b', ["a\\", "b"]),
        (r'a\"b', ['a"b']),
        (r'"a""b"', ['a"b']),
        ('""', [""]),
        (r'"C:\Evidence\$MFT"', [r"C:\Evidence\$MFT"]),
        ('-k "temp, tmp" --mp', ["-k", "temp, tmp", "--mp"]),
        ('a&b|c ^d %PATH%', ["a&b|c", "^d", "%PATH%"]),
    ],
)
def test_split_windows_args(text, expected):
    assert split_windows_args(text) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["PECmd.exe", "-d", r"C:\Cases\Case (1)\Evidence & More\Prefetch"],
        ["MFTECmd.exe", "-f", r"\\server\share\host 1\$MFT", "--csv", r"C:\out dir\csv"],
        ["X.exe", "-f", "C:\\پرونده\\شواهد\\NTUSER.DAT"],
        ["X.exe", 'a"b', "trailing\\", "back\\\\slash", ""],
    ],
)
def test_display_roundtrip(argv):
    assert split_windows_args(display_command(argv)) == argv
    assert subprocess.list2cmdline(argv) == display_command(argv)
