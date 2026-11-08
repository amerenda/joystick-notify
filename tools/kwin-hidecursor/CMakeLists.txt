cmake_minimum_required(VERSION 3.20)
project(jn_hidecursor LANGUAGES CXX)

find_package(ECM REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})

# Qt6 first: ECM's KDEInstallDirs6 asks the located Qt6 for its qmake.
find_package(Qt6 REQUIRED COMPONENTS Core)

include(KDEInstallDirs6)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)

find_package(KF6 REQUIRED COMPONENTS CoreAddons)
find_package(KWin REQUIRED COMPONENTS kwineffects)

set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

kcoreaddons_add_plugin(jn_hidecursor
    SOURCES jn_hidecursor.cpp
    INSTALL_NAMESPACE "kwin/effects/plugins")
target_link_libraries(jn_hidecursor PRIVATE KWin::kwin Qt6::Core KF6::CoreAddons)
