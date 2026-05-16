# Crea (o reemplaza) la tarea programada de Windows que ejecuta el pipeline
# diario de licitaciones cada mañana a las 7:00.
#
# Uso:
#     powershell -ExecutionPolicy Bypass -File scripts\programar_tarea_windows.ps1
#
# Para eliminar la tarea:
#     Unregister-ScheduledTask -TaskName "Higiofi-Licitaciones-Diario" -Confirm:$false

$ErrorActionPreference = "Stop"

$nombreTarea = "Higiofi-Licitaciones-Diario"
$raiz = (Resolve-Path "$PSScriptRoot\..").Path
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$script = Join-Path $raiz "scripts\pipeline_diario.py"

if (-not (Test-Path $python)) {
    Write-Error "No encuentro Python en $python. ¿Ejecutaste 'python -m venv .venv' y 'pip install -r requirements.txt'?"
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Error "No encuentro $script"
    exit 1
}

# Borra la tarea si ya existe
if (Get-ScheduledTask -TaskName $nombreTarea -ErrorAction SilentlyContinue) {
    Write-Host "Tarea ya existe — la elimino antes de recrearla..."
    Unregister-ScheduledTask -TaskName $nombreTarea -Confirm:$false
}

# Acción: ejecuta el python del venv con el script del pipeline
$accion = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $raiz

# Disparador: cada día a las 07:00
$disparador = New-ScheduledTaskTrigger -Daily -At 7:00am

# Si el equipo está apagado/dormido a esa hora, ejecutar al despertar
$ajustes = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

# Se ejecuta como el usuario actual sin abrir ventana visible
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $nombreTarea `
    -Description "Pipeline diario de licitaciones públicas Higiofi (PLACSP → DB → dashboard)" `
    -Action $accion `
    -Trigger $disparador `
    -Settings $ajustes `
    -Principal $principal | Out-Null

Write-Host ""
Write-Host "OK - Tarea '$nombreTarea' programada para ejecutarse cada día a las 07:00." -ForegroundColor Green
Write-Host ""
Write-Host "Comandos útiles:"
Write-Host "  Ejecutar ahora:   Start-ScheduledTask -TaskName '$nombreTarea'"
Write-Host "  Ver estado:       Get-ScheduledTask -TaskName '$nombreTarea' | Get-ScheduledTaskInfo"
Write-Host "  Ver el log:       Get-Content '$raiz\logs\pipeline_$((Get-Date).ToString('yyyy-MM-dd')).log' -Tail 30"
Write-Host "  Eliminar tarea:   Unregister-ScheduledTask -TaskName '$nombreTarea' -Confirm:`$false"
