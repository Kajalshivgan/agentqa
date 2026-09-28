*** Settings ***
Documentation     Verifies adjusting the display brightness slider via Settings from the app launcher.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Adjust Display Brightness From Launcher
    Restart App    com.android.car.carlauncher
    Tap On Description    Applications
    Tap On Id    com.android.car.carlauncher:id/app_icon    index=14
    Assert Visible    Connected devices
    Tap On Text    Display
    Assert Visible    Brightness level
    Set Slider    Brightness level    24
    Set Slider    Brightness level    77
    Tap On Description    Applications
    Assert Visible    Settings
