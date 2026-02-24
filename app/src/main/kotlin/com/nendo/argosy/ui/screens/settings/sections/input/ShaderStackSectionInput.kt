package com.nendo.argosy.ui.screens.settings.sections.input

import com.nendo.argosy.ui.input.InputHandler
import com.nendo.argosy.ui.input.InputResult
import com.nendo.argosy.ui.screens.settings.SettingsViewModel

internal class ShaderStackSectionInput(
    private val viewModel: SettingsViewModel
) : InputHandler {

    override fun onUp(): InputResult {
        viewModel.moveShaderParamFocus(-1)
        return InputResult.HANDLED
    }

    override fun onDown(): InputResult {
        viewModel.moveShaderParamFocus(1)
        return InputResult.HANDLED
    }

    override fun onLeft(): InputResult {
        viewModel.adjustShaderParam(-1)
        return InputResult.HANDLED
    }

    override fun onRight(): InputResult {
        viewModel.adjustShaderParam(1)
        return InputResult.HANDLED
    }

    override fun onConfirm(): InputResult {
        if (viewModel.shaderChainManager.shaderStack.selectedShaderParams.isNotEmpty()) {
            viewModel.resetShaderParam()
            return InputResult.HANDLED
        }
        return InputResult.UNHANDLED
    }

    override fun onContextMenu(): InputResult {
        viewModel.showShaderPicker()
        return InputResult.HANDLED
    }

    override fun onSecondaryAction(): InputResult {
        if (viewModel.shaderChainManager.shaderStack.entries.isNotEmpty()) {
            viewModel.removeShaderFromStack()
            return InputResult.HANDLED
        }
        return InputResult.UNHANDLED
    }

    override fun onPrevSection(): InputResult {
        val stack = viewModel.shaderChainManager.shaderStack
        if (stack.entries.isNotEmpty()) {
            viewModel.cycleShaderTab(-1)
            return InputResult.HANDLED
        }
        return InputResult.UNHANDLED
    }

    override fun onNextSection(): InputResult {
        val stack = viewModel.shaderChainManager.shaderStack
        if (stack.entries.isNotEmpty()) {
            viewModel.cycleShaderTab(1)
            return InputResult.HANDLED
        }
        return InputResult.UNHANDLED
    }

    override fun onPrevTrigger(): InputResult {
        viewModel.reorderShaderInStack(-1)
        return InputResult.HANDLED
    }

    override fun onNextTrigger(): InputResult {
        viewModel.reorderShaderInStack(1)
        return InputResult.HANDLED
    }
}
