*** Settings ***
Documentation     Recorded flow 'my_flow2' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
My Flow2
    Restart App    com.android.car.settings
    Tap On Text    Network & internet
    # NOTE: tapped before the screen was captured - replaying raw coordinates (possibly 'Brightness level', unverified)
    Wait Until Screen Is Stable
    Tap On Point    856    209
    # NOTE: tapped before the screen was captured - replaying raw coordinates (possibly 'Brightness level', unverified)
    Wait Until Screen Is Stable
    Tap On Point    1226    221
    Tap On Description    Hotspot toggle switch
