*** Settings ***
Documentation     One test driving both the ECU (SOME/IP) and the HMI (ADB/UIAutomator).
...               Needs a vsomeip service reachable at ${ECU_HOST}:${ECU_PORT}.
Library           engine.MobileUILibrary    device=${DEVICE}
Library           engine.SomeIpLibrary    timeout=3
Suite Teardown    Disconnect From SomeIP Service

*** Variables ***
${DEVICE}         ${None}
${ECU_HOST}       192.168.1.50
${ECU_PORT}       30509

*** Test Cases ***
ECU Setpoint Is Shown On HMI
    [Tags]    someip    hmi
    Connect To SomeIP Service    ${ECU_HOST}    ${ECU_PORT}
    Send SomeIP Request And Expect    0x1234    0x0001    expected_payload=00    payload=00 16
    Restart App    com.android.car.settings
    Wait Until Screen Is Stable
    Assert Visible    Display
