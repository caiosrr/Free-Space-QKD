# Registra a tarefa agendada de seguranca do mount num PC novo.
#
# Existe porque em 2026-09-29 a bancada da UFF mudou de PC, e tudo que parava o
# mount depois de um reinicio tinha sido registrado a mao no PC antigo, sem os
# comandos guardados em lugar nenhum. Rode de novo a cada troca de maquina.
#
# O que registra (ver Anotacoes/06_seguranca_do_mount.md, secao 9):
#
#   "Parar mount no boot"   como SYSTEM, na inicializacao, SEM precisar de login:
#                           parar_mount.py --via serial --aguardar 600.
#                           E a defesa real contra o reinicio do Windows Update.
#                           Roda sem janela (SYSTEM nao tem tela).
#
# O que REMOVE, se existir (mudanca de 2026-10-09, a pedido do laboratorio, que
# via um console abrindo de tempos em tempos no PC compartilhado):
#
#   "Parar mount no logon"  esperava o servidor ASCOM, que nao abre sozinho:
#                           na pratica ficava 10 min com uma janela e desistia.
#   "Publicar estado do PC" o aviso pelo Telegram a cada 15 min. Agora o tracker
#                           o sobe sem janela, so durante a sessao.
#
# E confere o reinicio automatico apos tela azul, que transforma um travamento
# com tela azul em reinicio, e portanto na tarefa de boot.
#
# Uso, num PowerShell COMO ADMINISTRADOR, da pasta Codigos:
#
#   powershell -ExecutionPolicy Bypass -File diversos\instalacao\registrar_tarefas_seguranca.ps1
#
# Rodar de novo substitui a tarefa (-Force); nao duplica. Rode de novo tambem
# quando o programa da tarefa mudar de nome: em 2026-10-10 o parar_mount_direto.py
# entrou no parar_mount.py, e a tarefa registrada antes aponta para um arquivo
# que deixou de existir.

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
# A tarefa aponta para um arquivo; se ele nao existir, ela falha em silencio a
# cada boot. Melhor recusar aqui, com o operador olhando.
$programa = Join-Path $codigos "programas_principais\parar_mount.py"
if (-not (Test-Path $programa)) {
    Write-Host "Nao achei $programa; nada foi registrado." -ForegroundColor Red
    exit 1
}
& $python -c "import serial" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "O .venv nao tem pyserial: a parada pela serial nao funcionaria." -ForegroundColor Red
    Write-Host "Rode: $python -m pip install -r requirements.txt"
    exit 1
}

# Como a parada vai achar o mount. So a identidade USB garante que nenhum
# aparelho de outro grupo recebe bytes; ver modulos\configuracoes\serial_mount.py.
Push-Location $codigos
$como = & $python -c "from modulos.configuracoes import serial_mount as c; print('usb' if c.USB_VID and c.USB_PID else ('porta' if c.PORTA_SERIAL else 'nada'))"
Pop-Location
if ($como -ne "usb") {
    Write-Host "ATENCAO: a identidade USB do mount nao esta configurada." -ForegroundColor Yellow
    if ($como -eq "porta") {
        Write-Host "  Hoje a parada usa um numero de porta fixo, que o Windows pode trocar."
    } else {
        Write-Host "  Sem ela a parada pergunta a identidade em TODAS as portas seriais,"
        Write-Host "  inclusive as de equipamentos de outros grupos."
    }
    Write-Host "  Aparelhos deste PC (so listados, nenhuma porta aberta):`n"
    & $python -m serial.tools.list_ports -v
    Write-Host "`n  Copie a linha do mount (VID:PID e SER) para"
    Write-Host "  modulos\configuracoes\serial_mount_local.py, como no .exemplo.py ao lado,"
    Write-Host "  e rode este script de novo.`n"
}
Write-Host "Codigos : $codigos"
Write-Host "Python  : $python`n"

$config = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

# 1. Boot, como SYSTEM, pela serial: age com a maquina trancada.
#    SEM NADA CONFIGURADO, NAO registra: a parada cairia na descoberta, que
#    pergunta :GVP# em todas as portas a cada boot, antes de o programa do outro
#    grupo abrir as dele. Achado em 2026-10-12 no PC da UFF, que estava assim
#    desde 2026-09-29. Uma tarefa ja registrada e removida.
if ($como -eq "nada") {
    if (Get-ScheduledTask -TaskName "Parar mount no boot" -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName "Parar mount no boot" -Confirm:$false
        Write-Host "ok  Parar mount no boot REMOVIDA (nada configurado)" -ForegroundColor Yellow
    }
    Write-Host "--  Parar mount no boot NAO registrada: sem serial_mount_local.py ela" -ForegroundColor Yellow
    Write-Host "    mandaria bytes a todas as portas seriais. Configure a identidade USB"
    Write-Host "    do mount (com ele ligado) e rode este script de novo ANTES da"
    Write-Host "    proxima sessao do tracker: ate la, nada para o mount depois de um"
    Write-Host "    reinicio do PC."
} else {
    $acao = New-ScheduledTaskAction -Execute $python -WorkingDirectory $codigos `
        -Argument "programas_principais\parar_mount.py --via serial --aguardar 600"
    $gatilho = New-ScheduledTaskTrigger -AtStartup
    $quem = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName "Parar mount no boot" -Action $acao -Trigger $gatilho `
        -Principal $quem -Settings $config -Force | Out-Null
    Write-Host "ok  Parar mount no boot (SYSTEM, serial)"
}

# 2. As tarefas antigas, que abriam janela na sessao de quem estivesse logado.
foreach ($nome in "Parar mount no logon", "Publicar estado do PC") {
    if (Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $nome -Confirm:$false
        Write-Host "ok  $nome REMOVIDA"
    } else {
        Write-Host "--  $nome nao existia"
    }
}

# 3. Tela azul vira reinicio, e o reinicio aciona a tarefa de boot. Sem isto um
#    travamento com tela azul deixaria o PC parado na tela de erro, e o mount
#    andando. Costuma vir ligado, mas num PC novo ninguem conferiu.
$recuperacao = Get-CimInstance Win32_OSRecoveryConfiguration
if (-not $recuperacao.AutoReboot) {
    Set-CimInstance -InputObject $recuperacao -Property @{ AutoReboot = $true }
    Write-Host "ok  Reinicio automatico apos tela azul: LIGADO agora (estava desligado)"
} else {
    Write-Host "ok  Reinicio automatico apos tela azul: ja estava ligado"
}

Write-Host "`nAviso pelo Telegram: agora o tracker o sobe sozinho, sem janela, se as"
Write-Host "variaveis QKD_TELEGRAM_TOKEN e QKD_TELEGRAM_CHAT existirem. Defina uma vez,"
Write-Host "num PowerShell COMUM (nao este), com setx; ver publicar_estado_pc.py."

Write-Host "`nConferencia:"
Get-ScheduledTask -TaskName "Parar mount no boot", "Parar mount no logon", "Publicar estado do PC" `
    -ErrorAction SilentlyContinue | Select-Object TaskName, State | Format-Table -AutoSize
$boot = Get-ScheduledTask -TaskName "Parar mount no boot" -ErrorAction SilentlyContinue
if ($boot) { $boot.Actions | Select-Object Execute, Arguments | Format-List }
