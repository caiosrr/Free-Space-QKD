# Registra as tarefas agendadas de seguranca do mount num PC novo.
#
# Existe porque em 2026-09-29 a bancada da UFF mudou de PC, e tudo que parava o
# mount depois de um reinicio tinha sido registrado a mao no PC antigo, sem os
# comandos guardados em lugar nenhum. Rode de novo a cada troca de maquina.
#
# O que registra (ver Anotacoes/06_seguranca_do_mount.md, secao 9):
#
#   "Parar mount no boot"   como SYSTEM, na inicializacao, SEM precisar de login:
#                           parar_mount_direto.py --aguardar 600, pela serial.
#                           E a defesa real contra o reinicio do Windows Update.
#   "Parar mount no logon"  no login do usuario: parar_mount.py --aguardar 600,
#                           pelo servidor ASCOM, que so sobe com alguem logado.
#   "Publicar estado do PC" a cada 15 min, avisa pelo Telegram se a sessao do
#                           tracker parou. So e registrada se o token for dado.
#
# E confere o reinicio automatico apos tela azul, que transforma um travamento
# com tela azul em reinicio, e portanto na tarefa de boot.
#
# Uso, num PowerShell COMO ADMINISTRADOR, da pasta Codigos:
#
#   powershell -ExecutionPolicy Bypass -File diversos\instalacao\registrar_tarefas_seguranca.ps1
#   powershell -ExecutionPolicy Bypass -File diversos\instalacao\registrar_tarefas_seguranca.ps1 -TelegramToken "123:ABC" -TelegramChat "456"
#
# Rodar de novo substitui as tarefas (-Force); nao duplica.

param(
    [string]$TelegramToken = "",
    [string]$TelegramChat = ""
)

$ErrorActionPreference = "Stop"

$principalAdmin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principalAdmin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Rode este script num PowerShell COMO ADMINISTRADOR." -ForegroundColor Red
    exit 1
}

$codigos = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $codigos ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Nao achei o Python do ambiente em $python" -ForegroundColor Red
    Write-Host "Crie o .venv e instale o requirements.txt antes."
    exit 1
}
& $python -c "import serial" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "O .venv nao tem pyserial: a parada pela serial nao funcionaria." -ForegroundColor Red
    Write-Host "Rode: $python -m pip install -r requirements.txt"
    exit 1
}
$portaLocal = Join-Path $codigos "modulos\configuracoes\serial_mount_local.py"
if (-not (Test-Path $portaLocal)) {
    Write-Host "ATENCAO: porta serial do mount nao configurada ($portaLocal)." -ForegroundColor Yellow
    Write-Host "  Sem ela a parada de emergencia pergunta a identidade em TODAS as portas"
    Write-Host "  seriais, inclusive as de equipamentos de outros grupos. Num PC compartilhado,"
    Write-Host "  crie o arquivo a partir de serial_mount_local.exemplo.py antes de seguir.`n"
}
Write-Host "Codigos : $codigos"
Write-Host "Python  : $python`n"

$config = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

# 1. Boot, como SYSTEM, pela serial: age com a maquina trancada.
$acao = New-ScheduledTaskAction -Execute $python -WorkingDirectory $codigos `
    -Argument "programas_principais\parar_mount_direto.py --aguardar 600"
$gatilho = New-ScheduledTaskTrigger -AtStartup
$quem = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName "Parar mount no boot" -Action $acao -Trigger $gatilho `
    -Principal $quem -Settings $config -Force | Out-Null
Write-Host "ok  Parar mount no boot (SYSTEM, serial)"

# 2. Logon, na sessao do usuario, pelo ASCOM.
$usuario = "$env:USERDOMAIN\$env:USERNAME"
$acao = New-ScheduledTaskAction -Execute $python -WorkingDirectory $codigos `
    -Argument "programas_principais\parar_mount.py --aguardar 600"
$gatilho = New-ScheduledTaskTrigger -AtLogOn -User $usuario
$quem = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "Parar mount no logon" -Action $acao -Trigger $gatilho `
    -Principal $quem -Settings $config -Force | Out-Null
Write-Host "ok  Parar mount no logon ($usuario, ASCOM)"

# 3. Aviso pelo Telegram, so com o token.
if ($TelegramToken -and $TelegramChat) {
    $acao = New-ScheduledTaskAction -Execute $python -WorkingDirectory $codigos `
        -Argument "programas_principais\publicar_estado_pc.py --telegram-token $TelegramToken --telegram-chat $TelegramChat"
    $gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
    # Limited e na sessao do usuario de proposito: como SYSTEM a tarefa nao
    # enxergaria a ociosidade do teclado.
    $quem = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName "Publicar estado do PC" -Action $acao -Trigger $gatilho `
        -Principal $quem -Settings $config -Force | Out-Null
    Write-Host "ok  Publicar estado do PC (a cada 15 min)"
} else {
    Write-Host "--  Publicar estado do PC NAO registrada: faltou -TelegramToken e -TelegramChat"
}

# 4. Tela azul vira reinicio, e o reinicio aciona a tarefa de boot. Sem isto um
#    travamento com tela azul deixaria o PC parado na tela de erro, e o mount
#    andando. Costuma vir ligado, mas num PC novo ninguem conferiu.
$recuperacao = Get-CimInstance Win32_OSRecoveryConfiguration
if (-not $recuperacao.AutoReboot) {
    Set-CimInstance -InputObject $recuperacao -Property @{ AutoReboot = $true }
    Write-Host "ok  Reinicio automatico apos tela azul: LIGADO agora (estava desligado)"
} else {
    Write-Host "ok  Reinicio automatico apos tela azul: ja estava ligado"
}

Write-Host "`nConferencia:"
Get-ScheduledTask -TaskName "Parar mount no boot", "Parar mount no logon", "Publicar estado do PC" `
    -ErrorAction SilentlyContinue | Select-Object TaskName, State | Format-Table -AutoSize
