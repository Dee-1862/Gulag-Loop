# One-shot setup for the zombie bot on a new Windows + NVIDIA PC. Run from the unzipped folder:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }

Step "Checking prerequisites"
$py = (Get-Command py -ErrorAction SilentlyContinue)
if ($py) { $python = "py"; $pyArgs = @("-3.12") } else { $python = "python"; $pyArgs = @() }
& $python @pyArgs --version
if (-not (Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe") -and
    -not (Test-Path "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe")) {
    throw "Google Chrome is required (the bot drives your installed Chrome). Install it and rerun."
}
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader

Step "Creating .venv"
if (-not (Test-Path .venv)) { & $python @pyArgs -m venv .venv }
$vpy = ".\.venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip

Step "Installing PyTorch with CUDA 12.8 (~2.8 GB download)"
& $vpy -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

Step "Installing bot dependencies"
& $vpy -m pip install -r requirements-bundle.txt
& $vpy -m pip install --no-deps easyocr   # its opencv-headless pin would clash with opencv-python

Step "Checking CUDA"
& $vpy -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'; print('GPU:', torch.cuda.get_device_name(0))"

Step "Building the TensorRT engine for THIS GPU (falls back to .pt if this fails)"
try {
    & $vpy -c "from ultralytics import YOLO; print(YOLO('models/zombies_v1.pt').export(format='engine', imgsz=640, half=True, workspace=2, verbose=False))"
} catch { Write-Warning "TensorRT export failed; the bot will use models/zombies_v1.pt (slower, still works)." }

Step "Done"
Write-Host @"
Next:
  1) Plug the laptop in (battery mode makes the GPU ~4x slower).
  2) Calibrate the mouse once (opens Chrome, loads Kino, takes the mouse ~1 min):
       .\.venv\Scripts\python -m bot.worker calibrate
  3) Play the evolved style (F8 aborts a game, Esc in game frees the mouse):
       .\.venv\Scripts\python -m bot.worker run --strategy strategies.json --name best --games 3 --results my_results.json
"@
