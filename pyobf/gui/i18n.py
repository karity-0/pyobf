STRINGS = {
    "open": ("Open file", "파일 열기"), "project_open": ("Open project", "프로젝트 열기"),
    "save_input": ("Save input", "입력 저장"), "build": ("Protect", "변환"),
    "copy": ("Copy output", "출력 복사"), "save": ("Save output", "출력 저장"),
    "run_input": ("Run input", "입력 실행"), "run_output": ("Run output", "출력 실행"),
    "stop": ("Stop execution", "실행 중지"), "preferences": ("Preferences", "설정"),
    "sidebar": ("Toggle workspace", "작업 공간 표시"), "project": ("PROJECT", "프로젝트"),
    "recent_files": ("RECENT FILES", "최근 파일"), "recent_projects": ("RECENT PROJECTS", "최근 프로젝트"),
    "input": ("SOURCE", "입력"), "output": ("PROTECTED", "출력"),
    "console": ("CONSOLE", "콘솔"), "untitled": ("Untitled.py", "새 파일.py"),
    "output_subject": ("Build output", "변환 결과"), "no_project": ("Open a folder to explore", "폴더를 열어 시작하세요"),
    "no_recents": ("Nothing here yet", "아직 기록이 없습니다"), "clear": ("Clear console", "콘솔 지우기"),
    "ready": ("Ready", "준비"), "analyzing": ("Building…", "분석 중…"),
    "unchanged": ("Original preserved", "원본 유지"), "changes": ("{count} changes", "변환 {count}곳"),
    "copied": ("Copied", "복사됨"), "saved": ("Saved", "저장됨"),
    "syntax": ("Check the syntax", "구문을 확인하세요"), "analysis_failed": ("Build failed", "분석 실패"),
    "open_failed": ("Unable to open file", "파일을 열 수 없습니다"), "save_failed": ("Unable to save", "저장 실패"),
    "project_failed": ("Project folder is unavailable", "프로젝트 폴더를 찾을 수 없습니다"),
    "running": ("Running {target}…", "{target} 실행 중…"), "finished": ("Finished · exit {code}", "실행 완료 · 종료 코드 {code}"),
    "stopped": ("Stopped", "중지됨"), "run_failed": ("Unable to start Python", "Python을 실행할 수 없습니다"),
    "stdin": ("Standard input · Enter to send", "표준 입력 · Enter로 전송"),
    "theme": ("Appearance", "테마"), "language": ("Language", "언어"), "done": ("Done", "완료"),
    "white": ("White", "화이트"), "dark": ("Dark", "다크"), "crystal": ("Crystal", "크리스탈"), "ocean": ("Ocean", "오션"),
    "dracula": ("Dracula", "드라큘라"), "mythic": ("Mythic", "미스틱"), "crimson": ("Crimson", "크림슨"),
    "settings_failed": ("Unable to save preferences", "설정을 저장할 수 없습니다"),
    "closing": ("Wait for the build to finish", "분석이 끝난 뒤 창을 닫을 수 있습니다"),
    "unsaved": ("Unsaved changes", "저장하지 않은 변경"),
    "discard": ("Discard the current edits and open another file?", "현재 편집 내용을 버리고 다른 파일을 여시겠습니까?"),
    "placeholder": ('Paste Python code or open a file\n\n@{"secret"}\n@protect_start(cff, junk) … @protect_end', 'Python 코드를 붙여넣거나 파일을 여세요\n\n@{"secret"}\n@protect_start(cff, junk) … @protect_end'),
    "console_hint": ("Run either editor to see output here.", "입력 또는 출력을 실행하면 결과가 여기에 표시됩니다."),
}


def tr(key, language="ko", **values):
    pair = STRINGS.get(key, (key, key))
    text = pair[0 if language == "en" else 1]
    return text.format(**values) if values else text


DIAGNOSTICS = {
    "protect 시작과 끝은 같은 Python 블록 안에 있어야 합니다": "Protection markers must belong to the same Python block",
    "protect 매크로는 독립된 줄에 써야 합니다": "Protection markers must be on their own lines",
    "protect 매크로 옵션은 한 줄에 써야 합니다": "Protection options must fit on one line",
    "@protect_start(cff, junk) 형식으로 써야 합니다": "Use @protect_start(cff, junk)",
    "protect 옵션으로 cff 또는 junk를 지정하세요": "Specify cff or junk as a protection option",
    "지원하는 protect 옵션은 cff, junk입니다": "Supported protection options: cff, junk",
    "protect 옵션이 중복되었습니다": "Duplicate protection option",
    "종료 매크로는 @protect_end로 써야 합니다": "Use @protect_end to close a protected region",
    "@protect_end에 대응하는 @protect_start가 없습니다": "@protect_end has no matching @protect_start",
    "protect 시작과 끝의 들여쓰기가 같아야 합니다": "Protection markers must have matching indentation",
    "protect 영역을 닫는 @protect_end가 필요합니다": "The protected region needs a closing @protect_end",
    "문자열 매크로를 닫는 }가 필요합니다": "The string macro needs a closing }",
    "@{...}에는 문자열 리터럴만 넣을 수 있습니다": "@{...} accepts string literals only",
}


def diagnostic(message, language):
    if language == "en":
        for korean, english in DIAGNOSTICS.items():
            message = message.replace(korean, english)
    return message
