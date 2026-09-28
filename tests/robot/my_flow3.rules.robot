*** Settings ***
Documentation     Recorded flow 'my_flow3' (rule-based conversion).
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
My Flow3
    Restart App    com.android.car.settings
    # system bar (BottomCarSystemBar): outside the app UI dump, raw coordinates
    Wait Until Screen Is Stable
    Tap On Point    707    760
    Assert Visible    Appium Settings
    Tap On Text    Settings
    Assert Visible    Connected devices
    Wait Until Screen Is Stable
    Swipe    342    471    342    218    100
    # NOTE: tapped before the screen was captured - replaying raw coordinates
    Wait Until Screen Is Stable
    Swipe    342    471    342    151    100
    # NOTE: tapped before the screen was captured - replaying raw coordinates
    Wait Until Screen Is Stable
    Swipe    342    471    342    44    100
    # NOTE: tapped before the screen was captured - replaying raw coordinates
    Wait Until Screen Is Stable
    Swipe    342    471    342    0    285
    Tap On Text    System
    Assert Visible    Languages & input
    Tap On Text    Languages & input
    Assert Visible    Languages
    Tap On Text    Languages
    Assert Visible    Suggested
    Tap On Text    English (United States)
    Assert Visible    Autofill service
    # system bar (BottomCarSystemBar): outside the app UI dump, raw coordinates
    Wait Until Screen Is Stable
    Tap On Point    730    763
    # system bar (BottomCarSystemBar): outside the app UI dump, raw coordinates
    Wait Until Screen Is Stable
    Tap On Point    693    725
    Assert Visible    Appium Settings
