*** Settings ***
Documentation     Verifies navigating from the car launcher into the Settings app.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Open Settings App From Launcher
    Restart App    com.android.car.carlauncher
    Tap On Description    Applications
    Assert Visible    Settings
    Tap On Text    Settings
    Assert Visible    Connected devices
