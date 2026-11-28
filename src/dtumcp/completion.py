"""Shell completion scripts for dtumcp: zsh, bash and PowerShell.

Each script is written out whole, commands, flags and all, because dtumcp
takes over half a second to start: too slow to ask on every Tab. Course
numbers are the one thing looked up as you type, from the names of the
saved course folders ("02231 Cryptography fundamentals, Fall 2026").

spec is a list of (command, what it does, its flags, what its first argument
is): "course" for a course number, "file" for a path, a list of the words it
takes, or "" for anything else.
"""

SHELLS = ("zsh", "bash", "powershell")


def _sh(text: str) -> str:
    """text as one single-quoted word, for zsh and bash."""
    return "'" + text.replace("'", "'\\''") + "'"


def _ps(text: str) -> str:
    """text as one single-quoted PowerShell string."""
    return "'" + text.replace("'", "''") + "'"


def _kind(arg) -> tuple[str, str]:
    """An argument's kind and, for a list of words, the words."""
    return ("words", " ".join(arg)) if isinstance(arg, list) else (arg, "")


BASH = r"""# dtumcp completion for bash. In ~/.bashrc: eval "$(dtumcp completions bash)"
_dtumcp_courses() {
    local d n
    for d in @DOWNLOADS@/[0-9][0-9][0-9][0-9][0-9]\ *; do
        [[ -d $d ]] || continue
        n=${d##*/}
        printf '%s\n' "${n%% *}"
    done
}
_dtumcp() {
    local cur=${COMP_WORDS[COMP_CWORD]} i=1 flags="" kind="" choices=""
    [[ ${COMP_WORDS[1]} == --json ]] && i=2
    if (( COMP_CWORD == i )); then
        COMPREPLY=($(compgen -W "@COMMANDS@ --json --help" -- "$cur"))
        return
    fi
    case ${COMP_WORDS[i]} in
@CASES@
    esac
    if [[ $cur == -* ]]; then
        COMPREPLY=($(compgen -W "$flags --args" -- "$cur"))
    elif (( COMP_CWORD == i + 1 )) && [[ $kind == course ]]; then
        COMPREPLY=($(compgen -W "$(_dtumcp_courses)" -- "$cur"))
    elif (( COMP_CWORD == i + 1 )) && [[ $kind == words ]]; then
        COMPREPLY=($(compgen -W "$choices" -- "$cur"))
    elif [[ $kind == file ]]; then
        compopt -o filenames 2>/dev/null
        COMPREPLY=($(compgen -f -- "$cur"))
    fi
}
complete -F _dtumcp dtumcp
"""

ZSH = r"""#compdef dtumcp
# dtumcp completion for zsh. In ~/.zshrc, after compinit (after oh-my-zsh.sh):
# eval "$(dtumcp completions zsh)", or save it as _dtumcp in a folder on $fpath.
_dtumcp() {
    local -a commands courses flags
    local i=2 kind="" choices="" d n
    commands=(
@COMMANDS@
    )
    [[ $words[2] == --json ]] && i=3
    if (( CURRENT == i )); then
        _describe command commands
        (( i == 2 )) && compadd -- --json --help
        return
    fi
    case $words[i] in
@CASES@
    esac
    if [[ $PREFIX == -* ]]; then
        compadd -- $flags --args
    elif (( CURRENT == i + 1 )) && [[ $kind == course ]]; then
        for d in @DOWNLOADS@/[0-9][0-9][0-9][0-9][0-9]\ *(N/); do
            n=${d:t}
            courses+=("${n%% *}:${n#* }")
        done
        _describe course courses
    elif (( CURRENT == i + 1 )) && [[ $kind == words ]]; then
        compadd -- ${=choices}
    elif [[ $kind == file ]]; then
        _files
    fi
}
if [[ $zsh_eval_context[-1] == loadautofunc ]]; then
    _dtumcp "$@"
else
    compdef _dtumcp dtumcp
fi
"""

POWERSHELL = r"""# dtumcp completion for PowerShell. In $PROFILE:
# dtumcp completions powershell | Out-String | Invoke-Expression
Register-ArgumentCompleter -Native -CommandName dtumcp, dtumcp.exe -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $commands = [ordered]@{
@COMMANDS@
    }
    # The words before the one being completed.
    $words = @($commandAst.CommandElements | Where-Object { $_.Extent.EndOffset -le $cursorPosition } |
        ForEach-Object { $_.ToString() })
    if ($wordToComplete) { $words = @($words | Select-Object -SkipLast 1) }
    $i = if ($words.Count -gt 1 -and $words[1] -eq '--json') { 2 } else { 1 }
    $found = @()
    if ($words.Count -eq $i) {
        $found = @($commands.Keys | ForEach-Object { [pscustomobject]@{ Text = $_; Tip = $commands[$_].About } })
        if ($i -eq 1) { $found += @('--json', '--help' | ForEach-Object { [pscustomobject]@{ Text = $_; Tip = $_ } }) }
    } elseif ($commands.Contains($words[$i])) {
        $cmd = $commands[$words[$i]]
        $names = @()
        if ($wordToComplete -like '-*') {
            $names = @($cmd.Flags) + '--args'
        } elseif ($words.Count -eq $i + 1 -and $cmd.Kind -eq 'course') {
            $names = @(Get-ChildItem -LiteralPath @DOWNLOADS@ -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { ($_.Name -split ' ', 2)[0] } | Where-Object { $_ -match '^\d{5}$' })
        } elseif ($words.Count -eq $i + 1 -and $cmd.Kind -eq 'words') {
            $names = @($cmd.Choices)
        }
        # Nothing found leaves PowerShell to complete a path, as a file argument wants.
        $found = @($names | Sort-Object -Unique | ForEach-Object { [pscustomobject]@{ Text = $_; Tip = $_ } })
    }
    $found | Where-Object { $_.Text -like "$wordToComplete*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_.Text, $_.Text, 'ParameterValue', $_.Tip)
    }
}
"""


def script(shell: str, spec: list, downloads) -> str:
    """The completion script for shell, one of SHELLS."""
    if shell == "bash":
        cases = []
        for name, _, flags, arg in spec:
            kind, choices = _kind(arg)
            cases.append(f"        {name}) flags={_sh(' '.join(flags))}; kind={kind}; choices={_sh(choices)};;")
        commands = " ".join(name for name, *_ in spec)
        return (BASH.replace("@COMMANDS@", commands).replace("@CASES@", "\n".join(cases))
                .replace("@DOWNLOADS@", _sh(str(downloads))))
    if shell == "zsh":
        cases = []
        for name, _, flags, arg in spec:
            kind, choices = _kind(arg)
            cases.append(f"        {name}) flags=({' '.join(map(_sh, flags))}); kind={kind}; choices={_sh(choices)};;")
        commands = "\n".join(f"        {_sh(f'{name}:{about}')}" for name, about, *_ in spec)
        return (ZSH.replace("@COMMANDS@", commands).replace("@CASES@", "\n".join(cases))
                .replace("@DOWNLOADS@", _sh(str(downloads))))
    if shell == "powershell":
        entries = []
        for name, about, flags, arg in spec:
            kind, choices = _kind(arg)
            entries.append(f"        {_ps(name)} = @{{ About = {_ps(about)}; Flags = @({', '.join(map(_ps, flags))}); "
                           f"Kind = {_ps(kind)}; Choices = @({', '.join(map(_ps, choices.split()))}) }}")
        return POWERSHELL.replace("@COMMANDS@", "\n".join(entries)).replace("@DOWNLOADS@", _ps(str(downloads)))
    raise RuntimeError(f"completions takes {', '.join(SHELLS[:-1])} or {SHELLS[-1]}, not {shell!r}.")
