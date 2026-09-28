*** Settings ***
Documentation     Verifies adjusting display brightness and toggling adaptive brightness in car Settings.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Adjust Display Brightness And Toggle Adaptive Brightness
    Restart App    com.android.car.carlauncher
    Tap On Description    Applications
    Tap On Text    Settings
    Assert Visible    Connected devices
    Tap On Text    Display
    Set Slider    Brightness level    21
    Set Slider    Brightness level    64
    Set Slider    Brightness level    86
    Tap On Text    Adaptive brightness
    Tap On Text    Adaptive brightness
    Tap On Description    Applications
    Assert Visible    My Application
