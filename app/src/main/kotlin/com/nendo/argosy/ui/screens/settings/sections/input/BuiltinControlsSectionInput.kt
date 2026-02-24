package com.nendo.argosy.ui.screens.settings.sections.input

import com.nendo.argosy.data.platform.PlatformWeightRegistry
import com.nendo.argosy.ui.input.InputHandler
import com.nendo.argosy.ui.input.InputResult
import com.nendo.argosy.ui.input.SoundType
import com.nendo.argosy.ui.screens.settings.SettingsViewModel
import com.nendo.argosy.ui.screens.settings.sections.BuiltinControlsItem
import com.nendo.argosy.ui.screens.settings.sections.builtinControlsItemAtFocusIndex
import com.nendo.argosy.ui.screens.settings.sections.builtinControlsMaxFocusIndex

internal class BuiltinControlsSectionInput(
    private val viewModel: SettingsViewModel
) : InputHandler {

    override fun onConfirm(): InputResult {
        val state = viewModel.uiState.value
        val controlsResetAllIndex = builtinControlsMaxFocusIndex(
            state.builtinControls, state.builtinVideo, state.platformLibretro.platformSettings
        )
        val controlsBaseMax = builtinControlsMaxFocusIndex(state.builtinControls)
        if (state.focusedIndex == controlsResetAllIndex && controlsResetAllIndex > controlsBaseMax) {
            viewModel.resetAllPlatformControlSettings()
            return InputResult.handled(SoundType.SELECT)
        }

        return when (builtinControlsItemAtFocusIndex(state.focusedIndex, state.builtinControls)) {
            BuiltinControlsItem.Rumble -> {
                if (state.builtinVideo.isGlobalContext) {
                    viewModel.setBuiltinRumbleEnabled(!state.builtinControls.rumbleEnabled)
                } else {
                    val ps = state.builtinVideo.currentPlatformContext?.let {
                        state.platformLibretro.platformSettings[it.platformId]
                    }
                    val effective = ps?.rumbleEnabled ?: state.builtinControls.rumbleEnabled
                    viewModel.updatePlatformControlSetting("rumbleEnabled", !effective)
                }
                InputResult.handled(SoundType.TOGGLE)
            }
            BuiltinControlsItem.LimitHotkeysToPlayer1 -> {
                viewModel.setBuiltinLimitHotkeysToPlayer1(!state.builtinControls.limitHotkeysToPlayer1)
                InputResult.handled(SoundType.TOGGLE)
            }
            BuiltinControlsItem.AnalogAsDpad -> {
                if (state.builtinVideo.isGlobalContext) {
                    viewModel.setBuiltinAnalogAsDpad(!state.builtinControls.analogAsDpad)
                } else {
                    val pc = state.builtinVideo.currentPlatformContext
                    val ps = pc?.let { state.platformLibretro.platformSettings[it.platformId] }
                    val platformHasAnalog = pc != null && PlatformWeightRegistry.hasAnalogStick(pc.platformSlug)
                    val effective = ps?.analogAsDpad ?: !platformHasAnalog
                    viewModel.updatePlatformControlSetting("analogAsDpad", !effective)
                }
                InputResult.handled(SoundType.TOGGLE)
            }
            BuiltinControlsItem.DpadAsAnalog -> {
                if (state.builtinVideo.isGlobalContext) {
                    viewModel.setBuiltinDpadAsAnalog(!state.builtinControls.dpadAsAnalog)
                } else {
                    val ps = state.builtinVideo.currentPlatformContext?.let {
                        state.platformLibretro.platformSettings[it.platformId]
                    }
                    val effective = ps?.dpadAsAnalog ?: false
                    viewModel.updatePlatformControlSetting("dpadAsAnalog", !effective)
                }
                InputResult.handled(SoundType.TOGGLE)
            }
            BuiltinControlsItem.ControllerOrder -> {
                viewModel.showControllerOrderModal()
                InputResult.handled(SoundType.SELECT)
            }
            BuiltinControlsItem.InputMapping -> {
                viewModel.showInputMappingModal()
                InputResult.handled(SoundType.SELECT)
            }
            BuiltinControlsItem.Hotkeys -> {
                viewModel.showHotkeysModal()
                InputResult.handled(SoundType.SELECT)
            }
            else -> InputResult.UNHANDLED
        }
    }

    override fun onContextMenu(): InputResult {
        val state = viewModel.uiState.value
        if (state.builtinVideo.isGlobalContext) return InputResult.UNHANDLED
        val item = builtinControlsItemAtFocusIndex(state.focusedIndex, state.builtinControls)
        val platformContext = state.builtinVideo.currentPlatformContext
        val ps = platformContext?.let { state.platformLibretro.platformSettings[it.platformId] }
        val field = when (item) {
            BuiltinControlsItem.Rumble -> "rumbleEnabled"
            BuiltinControlsItem.AnalogAsDpad -> "analogAsDpad"
            BuiltinControlsItem.DpadAsAnalog -> "dpadAsAnalog"
            else -> null
        }
        if (field != null) {
            val hasOverride = when (item) {
                BuiltinControlsItem.Rumble -> ps?.rumbleEnabled != null
                BuiltinControlsItem.AnalogAsDpad -> ps?.analogAsDpad != null
                BuiltinControlsItem.DpadAsAnalog -> ps?.dpadAsAnalog != null
                else -> false
            }
            if (hasOverride) {
                viewModel.updatePlatformControlSetting(field, null)
                return InputResult.HANDLED
            }
        }
        return InputResult.UNHANDLED
    }

    override fun onPrevSection(): InputResult {
        val state = viewModel.uiState.value
        if (state.builtinVideo.availablePlatforms.isNotEmpty()) {
            viewModel.cyclePlatformContext(-1)
            return InputResult.HANDLED
        }
        return InputResult.UNHANDLED
    }

    override fun onNextSection(): InputResult {
        val state = viewModel.uiState.value
        if (state.builtinVideo.availablePlatforms.isNotEmpty()) {
            viewModel.cyclePlatformContext(1)
            return InputResult.HANDLED
        }
        return InputResult.UNHANDLED
    }
}
