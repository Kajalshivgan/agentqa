


*** Settings ***
Documentation     Smoke test for Assert Image Visible / Tap On Image / Assert Screenshot Matches
...               on the AAOS emulator. Not part of the recorded-flow suite: exercises the new
...               image verification keywords directly.
Library           engine.MobileUILibrary    device=${DEVICE}    timeout=10

*** Variables ***
${DEVICE}         ${None}

*** Test Cases ***
Find And Tap The Apps Grid Icon By Image
    Press Home
    Assert Image Visible    apps_grid_icon
    Tap On Image    apps_grid_icon
    Assert Visible    Appium Settings

Screenshot Matches Itself On A Stable Screen
    Press Home
    Wait Until Screen Is Stable
    Assert Screenshot Matches    home_screen    threshold=99
    Assert Screenshot Matches    home_screen    threshold=99

Screenshot Comparison Actually Fails On A Different Screen
    [Documentation]    Negative-path check: confirms the keyword can fail, not just pass.
    Press Home
    Tap On Image    apps_grid_icon
    Wait Until Screen Is Stable
    Run Keyword And Expect Error    Screenshot * is only * similar *
    ...    Assert Screenshot Matches    home_screen    threshold=99

Image Not Present Times Out And Fails
    [Documentation]    Negative-path check: the Home-screen dock icon genuinely isn't present
    ...    once inside the App Grid, so this must fail rather than false-match.
    Press Home
    Tap On Image    apps_grid_icon
    Assert Visible    Appium Settings
    Run Keyword And Expect Error    WaitTimeout: Image 'apps_grid_icon' not found*
    ...    Assert Image Visible    apps_grid_icon    timeout=2
