local deckhand = {}

local allowedLayouts = {
  develop = function()
    -- Site-specific window placement belongs in private Hammerspoon inventory.
    return { state = "unconfigured", layout = "develop" }
  end,
  meeting = function()
    return { state = "unconfigured", layout = "meeting" }
  end,
}

function deckhand.dispatch(action, target)
  if action ~= "workspace.apply" then
    return { ok = false, error = "unknown_action" }
  end
  local handler = allowedLayouts[target]
  if handler == nil then
    return { ok = false, error = "unknown_target" }
  end
  return { ok = true, result = handler() }
end

return deckhand
