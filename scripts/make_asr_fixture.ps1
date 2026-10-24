# Generate the synthetic ASR fixture used by tests/test_asr_fixture.py (Windows only).
# The audio is made with Windows text-to-speech, contains no personal data, and is
# not committed (*.wav is ignored by Git). Run from the repository root:
#   powershell -ExecutionPolicy Bypass -File scripts\make_asr_fixture.ps1
Add-Type -AssemblyName System.Speech
$dir = Join-Path $PSScriptRoot "..\tests\fixtures\audio"
New-Item -ItemType Directory -Force $dir | Out-Null
$path = Join-Path $dir "tts_clear.wav"
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile($path, $format)
$synth.Speak("The quarterly budget review is scheduled for Tuesday. I will submit the report by Friday. Please check the mobile layout before the demo.")
$synth.SetOutputToNull()
$synth.Dispose()
Write-Output "Wrote $path"
