*** Settings ***
Documentation     Recorded flow 'repro_app_grid' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Repro App Grid
    Restart App    com.android.car.carlauncher
    # system bar (BottomCarSystemBar): outside the app UI dump, raw coordinates
    Tap On Point    525    743
    Assert Visible    Settings
    Tap On Text    Settings
    Assert Visible    Display
    Tap On Text    Display
