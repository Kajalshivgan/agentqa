*** Settings ***
Documentation     Verifies navigating from the AAOS launcher into Settings, through the Display and Sound sections, and back to the launcher via the system app grid.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
AAOS 101 Display Brightness
    Restart App    com.android.car.carlauncher
    Tap On Text    Settings
    Assert Visible    Display
    Tap On Text    Display
    Assert Visible    Brightness level
    Tap On Text    Sound
    Assert Visible    Media volume
    # NOTE: Tap on system bar (Applications / app grid button)
    Tap On Point    537    761
    Assert Visible    Settings
