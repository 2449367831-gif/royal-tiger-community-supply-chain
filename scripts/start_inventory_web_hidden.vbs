Option Explicit

Dim shell, fso, root, launcher, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
launcher = fso.BuildPath(root, "scripts\run_inventory_server_hidden.ps1")
command = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & launcher & """"
shell.Run command, 0, False
