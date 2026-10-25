*** Settings ***
Library    engine.MobileUILibrary    device=${DEVICE}
Library    engine.SpeechLibrary      device=${DEVICE}

*** Variables ***
${DEVICE}    ${None}

*** Test Cases ***
Open Weather By Hotword
    [Documentation]    Full phrase. May fail if Voice Match rejects the synthetic voice.
    Press Home
    Speak                          Hey Google, open weather
    Wait Until Screen Is Stable
    Assert Visible                 Weather

Open Weather By Assistant Key
    [Documentation]    Hotword bypassed with KEYCODE_VOICE_ASSIST; only the command is spoken.
    Press Home
    Speak Command                  open weather
    Wait Until Screen Is Stable
    Assert Visible                 Weather
