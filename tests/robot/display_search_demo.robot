*** Settings ***
Documentation     Recorded flow 'display_search_demo' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Display Search Demo
    Restart App    com.android.car.settings
    Tap On Text    Display
    # NOTE: stale_snapshot: acted before the UI was re-captured; verify this step
    Tap On Text    Sound
    Assert Visible    Play media on
    Tap On Text    Display
    Assert Visible    Brightness level
    Tap On Description    Search
    Input Text    brightness
    Tap On Text    Adaptive brightness
    Assert Visible    Connected devices
