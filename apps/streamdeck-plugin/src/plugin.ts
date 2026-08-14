import streamDeck from "@elgato/streamdeck";

import { StatusAction } from "./actions/status.js";
import { TypedAction } from "./actions/typed-action.js";

streamDeck.logger.setLevel("info");
streamDeck.actions.registerAction(new StatusAction());
streamDeck.actions.registerAction(new TypedAction());
streamDeck.connect();
