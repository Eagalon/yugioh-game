import os
import re
import random
import json
from functools import partial
from collections import OrderedDict
import datetime
import collections
import struct
import sqlite3
import codecs
import weakref
import gsb
from gsb.intercept import Menu, Reader
from parsers import YesOrNo
import natsort
from twisted.python import log
from autobahn.twisted.websocket import listenWS
import duel as dm
from parsers import parser, duel_parser, LoginParser
import models
import game
import i18n
import websockets

__ = lambda s: s

class Server(gsb.Server):

  def __init__(self, *args, **kwargs):
    super(Server, self).__init__(self, *args, **kwargs)
    self.db = sqlite3.connect('locale/en/cards.cdb')
    self.db.row_factory = sqlite3.Row
    self.players = {}
    self.session_factory = models.setup()
    self.all_cards = [int(row[0]) for row in self.db.execute("select id from datas")]

  def on_connect(self, caller):
    ### for backwards compatibility ###
    caller.connection._ = lambda s: caller.connection.player._(s)
    caller.connection.player = None
    caller.connection.session = self.session_factory()
    caller.connection.web = False

  def on_disconnect(self, caller):
    con = caller.connection
    if not con.player:
      return
    del self.players[con.player.nickname.lower()]
    for pl in self.players.values():
      pl.notify(pl._("%s logged out.") % con.player.nickname)
    if con.player.watching:
      con.player.duel.watchers.remove(con.player)
      con.player.duel = None
    if con.player.duel:
      con.player.duel.player_disconnected(con.player)
    con.player.nickname = None

  def get_player(self, name):
    return self.players.get(name.lower())

  def get_all_players(self):
    return self.players.values()

  def start_duel(self, *players):
    players = list(players)
    random.shuffle(players)
    duel = Duel()
    duel.orig_nicknames = (players[0].nickname, players[1].nickname)
    duel.load_deck(0, players[0].deck['cards'])
    duel.load_deck(1, players[1].deck['cards'])
    for i, pl in enumerate(players):
      pl.notify(pl._("Duel created. You are player %d.") % i)
      pl.notify(pl._("Type help dueling for a list of usable commands."))
      pl.duel = duel
      pl.duel_player = i
      pl.parser = DuelParser
    duel.players = players
    if os.environ.get('DEBUG', 0):
      duel.start_debug()
    duel.start()
    reactor.callLater(0, process_duel, duel)

  # me being the caller (we don't want to address me)
  def guess_players(self, name, me):

    name = name[0].upper()+name[1:].lower()
    players = [self.get_player(p) for p in self.players.keys() if (p[0].upper()+p[1:].lower()) != me]
    i = 0

    while i < len(players):
      if players[i].nickname == name:
        # exact match means we will only return that player
        return [players[i]]
      elif players[i].nickname.startswith(name):
        i += 1
        continue
      else:
        del players[i]

    players.sort(key=lambda p: p.nickname)

    return players

  def announce_challenge(self, pl, text):
    if not pl.challenge:
      return
    pl.notify("Challenge: " + text)

class MyDuel(dm.Duel):
  def __init__(self, *args, **kwargs):
    super(MyDuel, self).__init__(*args, **kwargs)
    self.keep_processing = False
    self.to_ep = False
    self.to_m2 = False
    self.current_phase = 0
    self.watchers = []
    self.private = False
    self.cm.register_callback('draw', self.draw)
    self.cm.register_callback('shuffle', self.shuffle)
    self.cm.register_callback('phase', self.phase)
    self.cm.register_callback('new_turn', self.new_turn)
    self.cm.register_callback('idle', self.idle)
    self.cm.register_callback('select_place', self.select_place)
    self.cm.register_callback('select_chain', self.select_chain)
    self.cm.register_callback('summoning', self.summoning)
    self.cm.register_callback('flipsummoning', self.flipsummoning)
    self.cm.register_callback("select_battlecmd", self.select_battlecmd)
    self.cm.register_callback('attack', self.attack)
    self.cm.register_callback('begin_damage', self.begin_damage)
    self.cm.register_callback('end_damage', self.end_damage)
    self.cm.register_callback('decktop', self.decktop)
    self.cm.register_callback('battle', self.battle)
    self.cm.register_callback('damage', self.damage)
    self.cm.register_callback('hint', self.hint)
    self.cm.register_callback('select_card', self.select_card)
    self.cm.register_callback('move', self.move)
    self.cm.register_callback('select_option', self.select_option)
    self.cm.register_callback('recover', self.recover)
    self.cm.register_callback('select_tribute', partial(self.select_card, is_tribute=True))
    self.cm.register_callback('pos_change', self.pos_change)
    self.cm.register_callback('set', self.set)
    self.cm.register_callback("chaining", self.chaining)
    self.cm.register_callback('select_position', self.select_position)
    self.cm.register_callback('yesno', self.yesno)
    self.cm.register_callback('select_effectyn', self.select_effectyn)
    self.cm.register_callback('win', self.win)
    self.cm.register_callback('pay_lpcost', self.pay_lpcost)
    self.cm.register_callback('sort_chain', self.sort_chain)
    self.cm.register_callback('announce_attrib', self.announce_attrib)
    self.cm.register_callback('announce_card', self.announce_card)
    self.cm.register_callback('announce_number', self.announce_number)
    self.cm.register_callback('announce_card_filter', self.announce_card_filter)
    self.cm.register_callback('select_sum', self.select_sum)
    self.cm.register_callback('select_counter', self.select_counter)
    self.cm.register_callback('announce_race', self.announce_race)
    self.cm.register_callback('become_target', self.become_target)
    self.cm.register_callback('sort_card', self.sort_card)
    self.cm.register_callback('field_disabled', self.field_disabled)
    self.cm.register_callback('toss_coin', self.toss_coin)
    self.cm.register_callback('toss_dice', self.toss_dice)
    self.cm.register_callback('confirm_cards', self.confirm_cards)
    self.cm.register_callback('chain_solved', self.chain_solved)
    self.cm.register_callback('equip', self.equip)
    self.cm.register_callback('lpupdate', self.lpupdate)
    self.cm.register_callback('debug', self.debug)
    self.cm.register_callback('counters', self.counters)
    self.cm.register_callback('swap', self.swap)
    self.debug_mode = False
    self.players = [None, None]
    self.lp = [8000, 8000]
    self.started = False

  def swap(self, card1, card2):

    for p in self.watchers+self.players:
      for card in (card1, card2):
        plname = self.players[card.controller].nickname
        s = self.card_to_spec(p.duel_player, card)
        p.notify(p._("card {name} swapped control towards {plname} and is now located at {targetspec}.").format(plname=plname, targetspec=s, name=card.get_name(p)))

  def counters(self, card, type, count, added):

    for pl in self.players+self.watchers:

      stype = strings[pl.language]['counter'][type]

      if added:
         pl.notify(pl._("{amount} counters of type {counter} placed on {card}").format(amount=count, counter=stype, card=card.get_name(pl)))

      else:
         pl.notify(pl._("{amount} counters of type {counter} removed from {card}").format(amount=count, counter=stype, card=card.get_name(pl)))

  def decktop(self, player, card):
    player = self.players[player]
    for pl in self.players+self.watchers:
      if pl is player:
        pl.notify(pl._("you reveal your top deck card to be %s")%(card.get_name(pl)))
      else:
        pl.notify(pl._("%s reveals their top deck card to be %s")%(player.nickname, card.get_name(pl)))

  def draw(self, player, cards):
    pl = self.players[player]
    pl.notify(pl._("Drew %d cards:") % len(cards))
    for i, c in enumerate(cards):
      pl.notify("%d: %s" % (i+1, c.get_name(pl)))
    op = self.players[1 - player]
    op.notify(op._("Opponent drew %d cards.") % len(cards))
    for w in self.watchers:
      w.notify(w._("%s drew %d cards.") % (pl.nickname, len(cards)))

  def phase(self, phase):
    phases = {
      1: __('draw phase'),
      2: __('standby phase'),
      4: __('main1 phase'),
      8: __('battle start phase'),
      0x10: __('battle step phase'),
      0x20: __('damage phase'),
      0x40: __('damage calculation phase'),
      0x80: __('battle phase'),
      0x100: __('main2 phase'),
      0x200: __('end phase'),
    }
    phase_str = phases.get(phase, str(phase))
    for pl in self.players + self.watchers:
      pl.notify(pl._('entering %s.') % pl._(phase_str))
    self.current_phase = phase

  def new_turn(self, tp):
    self.tp = tp
    self.players[tp].notify(self.players[tp]._("Your turn."))
    op = self.players[1 - tp]
    op.notify(op._("%s's turn.") % self.players[tp].nickname)
    for w in self.watchers:
      w.notify(w._("%s's turn.") % self.players[tp].nickname)

  def idle(self, summonable, spsummon, repos, idle_mset, idle_set, idle_activate, to_bp, to_ep, cs):
    self.state = "idle"
    pl = self.players[self.tp]
    self.summonable = summonable
    self.spsummon = spsummon
    self.repos = repos
    self.idle_mset = idle_mset
    self.idle_set = idle_set
    self.idle_activate = idle_activate
    self.to_bp = bool(to_bp)
    self.to_ep = bool(to_ep)
    self.idle_action(pl)

  def idle_action(self, pl):
    def prompt():
      pl.notify(pl._("Select a card on which to perform an action."))
      pl.notify(pl._("h shows your hand, tab and tab2 shows your or the opponent's table, ? shows usable cards."))
      if self.to_bp:
        pl.notify(pl._("b: Enter the battle phase."))
      if self.to_ep:
        pl.notify(pl._("e: End phase."))
      pl.notify(DuelReader, r,
      no_abort=pl._("Invalid specifier. Retry."),
      prompt=pl._("Select a card:"),
      restore_parser=duel_parser)
    cards = []
    for i in (0, 1):
      for j in (dm.LOCATION_HAND, dm.LOCATION_MZONE, dm.LOCATION_SZONE, dm.LOCATION_GRAVE, dm.LOCATION_EXTRA):
        cards.extend(self.get_cards_in_location(i, j))
    specs = set(self.card_to_spec(self.tp, card) for card in cards)
    def r(caller):
      if caller.text == 'b' and self.to_bp:
        self.set_responsei(6)
        reactor.callLater(0, procduel, self)
        return
      elif caller.text == 'e' and self.to_ep:
        self.set_responsei(7)
        reactor.callLater(0, procduel, self)
        return
      elif caller.text == '?':
        self.show_usable(pl)
        return pl.notify(DuelReader, r,
        no_abort=pl._("Invalid specifier. Retry."),
        prompt=pl._("Select a card:"),
        restore_parser=duel_parser)
      if caller.text not in specs:
        pl.notify(pl._("Invalid specifier. Retry."))
        prompt()
        return
      loc, seq = self.cardspec_to_ls(caller.text)
      if caller.text.startswith('o'):
        plr = 1 - self.tp
      else:
        plr = self.tp
      card = self.get_card(plr, loc, seq)
      if not card:
        pl.notify(pl._("There is no card in that position."))
        prompt()
        return
      if plr == 1 - self.tp:
        if card.position in (0x8, 0xa):
          pl.notify(pl._("Face-down card."))
          return prompt()
      self.act_on_card(caller, card)
    prompt()

  def act_on_card(self, caller, card):
    pl = self.players[self.tp]
    name = card.get_name(pl)
    if card in self.idle_activate:
      card = self.idle_activate[self.idle_activate.index(card)]
    def prompt(menu=True):
      if not menu:
        return pl.notify(DuelReader, action, no_abort=pl._("Invalid command."), prompt=pl._("Select action for {card}").format(card=name), restore_parser=duel_parser)
      pl.notify(name)
      activate_count = self.idle_activate.count(card)
      if card in self.summonable:
        pl.notify("s: "+pl._("Summon this card in face-up attack position."))
      if card in self.idle_set:
        pl.notify("t: "+pl._("Set this card."))
      if card in self.idle_mset:
        pl.notify("m: "+pl._("Summon this card in face-down defense position."))
      if card in self.repos:
        pl.notify("r: "+pl._("reposition this card."))
      if card in self.spsummon:
        pl.notify("c: "+pl._("Special summon this card."))
      if activate_count > 0:
        effect_descriptions = []
        for i in range(activate_count):
          ind = self.idle_activate[self.idle_activate.index(card)+i].extra
          effect_descriptions.append(card.get_effect_description(pl, ind))

        if activate_count == 1:
          pl.notify("v: "+effect_descriptions[0])
        else:
          for i in range(activate_count):
            pl.notify("v"+chr(97+i)+": "+effect_descriptions[i])
      pl.notify("i: "+pl._("Show card info."))
      pl.notify("z: "+pl._("back."))
      pl.notify(DuelReader, action, no_abort=pl._("Invalid command."), prompt=pl._("Select action for {card}").format(card=name), restore_parser=duel_parser)
    def action(caller):
      if caller.text == 's' and card in self.summonable:
        self.set_responsei(self.summonable.index(card) << 16)
      elif caller.text == 't' and card in self.idle_set:
        self.set_responsei((self.idle_set.index(card) << 16) + 4)
      elif caller.text == 'm' and card in self.idle_mset:
        self.set_responsei((self.idle_mset.index(card) << 16) + 3)
      elif caller.text == 'r' and card in self.repos:
        self.set_responsei((self.repos.index(card) << 16) + 2)
      elif caller.text == 'c' and card in self.spsummon:
        self.set_responsei((self.spsummon.index(card) << 16) + 1)
      elif caller.text == 'i':
        self.show_info(card, pl)
        return prompt(False)
      elif caller.text == 'z':
        reactor.callLater(0, self.idle_action, pl)
        return
      elif caller.text.startswith('v'):
        activate_count = self.idle_activate.count(card)
        if len(caller.text)>2 or activate_count == 0 or (len(caller.text) == 1 and activate_count > 1) or (len(caller.text) == 2 and activate_count == 1):
          pl.notify(pl._("Invalid action."))
          prompt()
          return
        index = self.idle_activate.index(card)
        if len(caller.text) == 2:
          # parse the second letter
          try:
            o = ord(caller.text[1])
          except TypeError:
            o = -1
          ad = o - ord('a')
          if not (0 <= ad <= 25) or ad >= activate_count:
            pl.notify(pl._("Invalid action."))
            prompt()
            return
          index += ad
        self.set_responsei((index << 16) + 5)
      else:
        pl.notify(pl._("Invalid action."))
        prompt()
        return
      reactor.callLater(0, procduel, self)
    prompt()

  def show_usable(self, pl):
    summonable = natsort.natsorted([self.card_to_spec(pl.duel_player, card) for card in self.summonable])
    spsummon = natsort.natsorted([self.card_to_spec(pl.duel_player, card) for card in self.spsummon])
    repos = natsort.natsorted([self.card_to_spec(pl.duel_player, card) for card in self.repos])
    mset = natsort.natsorted([self.card_to_spec(pl.duel_player, card) for card in self.idle_mset])
    idle_set = natsort.natsorted([self.card_to_spec(pl.duel_player, card) for card in self.idle_set])
    idle_activate = natsort.natsorted([self.card_to_spec(pl.duel_player, card) for card in self.idle_activate])
    if summonable:
      pl.notify(pl._("Summonable in attack position: %s") % ", ".join(summonable))
    if mset:
      pl.notify(pl._("Summonable in defense position: %s") % ", ".join(mset))
    if spsummon:
      pl.notify(pl._("Special summonable: %s") % ", ".join(spsummon))
    if idle_activate:
      pl.notify(pl._("Activatable: %s") % ", ".join(idle_activate))
    if repos:
      pl.notify(pl._("Repositionable: %s") % ", ".join(repos))
    if idle_set:
      pl.notify(pl._("Settable: %s") % ", ".join(idle_set))

  def cardspec_to_ls(self, text):
    if text.startswith('o'):
      text = text[1:]
    r = re.search(r'^([a-z]+)(\d+)', text)
    if not r:
      return (None, None)
    if r.group(1) == 'h':
      l = dm.LOCATION_HAND
    elif r.group(1) == 'm':
      l = dm.LOCATION_MZONE
    elif r.group(1) == 's':
      l = dm.LOCATION_SZONE
    elif r.group(1) == 'g':
      l = dm.LOCATION_GRAVE
    elif r.group(1) == 'x':
      l = dm.LOCATION_EXTRA
    elif r.group(1) == 'r':
      l = dm.LOCATION_REMOVED
    else:
      return None, None
    return l, int(r.group(2)) - 1

  def pcl(self, name, cards):
    self.players[self.tp].notify(name+":")
    for card in cards:
      self.players[self.tp].notify(card.name)

  def select_place(self, player, count, flag):
    pl = self.players[player]
    specs = self.flag_to_usable_cardspecs(flag)
    if count == 1:
      pl.notify(pl._("Select place for card, one of %s.") % ", ".join(specs))
    else:
      pl.notify(pl._("Select %d places for card, from %s.") % (count, ", ".join(specs)))
    def r(caller):
      values = caller.text.split()
      if len(set(values)) != len(values):
        pl.notify(pl._("Duplicate values not allowed."))
        return pl.notify(DuelReader, r, no_abort=pl._("Invalid command"), restore_parser=duel_parser)
      if len(values) != count:
        pl.notify(pl._("Please enter %d values.") % count)
        return pl.notify(DuelReader, r, no_abort=pl._("Invalid command"), restore_parser=duel_parser)
      if any(value not in specs for value in values):
        pl.notify(pl._("Invalid cardspec. Try again."))
        pl.notify(DuelReader, r, no_abort=pl._("Invalid command"), restore_parser=duel_parser)
        return
      resp = b''
      for value in values:
        l, s = self.cardspec_to_ls(value)
        if value.startswith('o'):
          plr = 1 - player
        else:
          plr = player
        resp += bytes([plr, l, s])
      self.set_responseb(resp)
      reactor.callLater(0, procduel, self)
    pl.notify(DuelReader, r, no_abort=pl._("Invalid command"), restore_parser=duel_parser)

  def flag_to_usable_cardspecs(self, flag, reverse=False):
    pm = flag & 0xff
    ps = (flag >> 8) & 0xff
    om = (flag >> 16) & 0xff
    os = (flag >> 24) & 0xff
    zone_names = ('m', 's', 'om', 'os')
    specs = []
    for zn, val in zip(zone_names, (pm, ps, om, os)):
      for i in range(8):
        if reverse:
          avail = val & (1 << i) != 0
        else:
          avail = val & (1 << i) == 0
        if avail:
          specs.append(zn + str(i + 1))
    return specs

  def select_chain(self, player, size, spe_count, forced, chains):
    if size == 0 and spe_count == 0:
      self.keep_processing = True
      self.set_responsei(-1)
      return
    pl = self.players[player]
    self.chaining_player = player
    op = self.players[1 - player]
    if not op.seen_waiting:
      op.notify(op._("Waiting for opponent."))
      op.seen_waiting = True
    chain_cards = [c[1] for c in chains]
    specs = {}
    for i in range(len(chains)):
      card = chains[i][1]
      card.chain_index = i
      desc = chains[i][2]
      cs = self.card_to_spec(player, card)
      chain_count = chain_cards.count(card)
      if chain_count > 1:
        cs += chr(ord('a')+list(specs.values()).count(card))
      specs[cs] = card
      card.chain_spec = cs
      card.effect_description = card.get_effect_description(pl, desc, True)
    def prompt():
      if forced:
        pl.notify(pl._("Select chain:"))
      else:
        pl.notify(pl._("Select chain (c to cancel):"))
      for card in chain_cards:
        if card.effect_description == '':
          pl.notify("%s: %s" % (card.chain_spec, card.get_name(pl)))
        else:
          pl.notify("%s (%s): %s"%(card.chain_spec, card.get_name(pl), card.effect_description))
      if forced:
        prompt = pl._("Select card to chain:")
      else:
        prompt = pl._("Select card to chain (c = cancel):")
      pl.notify(DuelReader, r, no_abort=pl._("Invalid command."),
      prompt=prompt, restore_parser=duel_parser)
    def r(caller):
      if caller.text == 'c' and not forced:
        self.set_responsei(-1)
        reactor.callLater(0, procduel, self)
        return
      if caller.text.startswith('i'):
        info = True
        caller.text = caller.text[1:]
      else:
        info = False
      if caller.text not in specs:
        pl.notify(pl._("Invalid spec."))
        return prompt()
      card = specs[caller.text]
      idx = card.chain_index
      if info:
        self.show_info(card, pl)
        return prompt()
      self.set_responsei(idx)
      reactor.callLater(0, procduel, self)
    prompt()

  def select_option(self, player, options):
    pl = self.players[player]
    def select(caller, idx):
      self.set_responsei(idx)
      reactor.callLater(0, procduel, self)
    opts = []
    for opt in options:
      if opt > 10000:
        code = opt >> 4
        string = dm.Card.from_code(code).strings[opt & 0xf]
      else:
        string = "Unknown option %d" % opt
        string = strings[pl.language]['system'].get(opt, string)
      opts.append(string)
    m = Menu(pl._("Select option:"), no_abort=pl._("Invalid option."), persistent=True, prompt=pl._("Select option:"), restore_parser=duel_parser)
    for idx, opt in enumerate(opts):
      m.item(opt)(lambda caller, idx=idx: select(caller, idx))
    pl.notify(m)

  def summoning(self, card, special=False):
    if special:
      action = "Special summoning"
    else:
      action = "Summoning"
    nick = self.players[card.controller].nickname
    for pl in self.players + self.watchers:
      pos = card.get_position(pl)
      if special:
        pl.notify(pl._("%s special summoning %s (%d/%d) in %s position.") % (nick, card.get_name(pl), card.attack, card.defense, pos))
      else:
        pl.notify(pl._("%s summoning %s (%d/%d) in %s position.") % (nick, card.get_name(pl), card.attack, card.defense, pos))

  def select_battlecmd(self, player, activatable, attackable, to_m2, to_ep):
    self.state = "battle"
    self.activatable = activatable
    self.attackable = attackable
    self.to_m2 = bool(to_m2)
    self.to_ep = bool(to_ep)
    pl = self.players[player]
    self.display_battle_menu(pl)

  def display_battle_menu(self, pl):
    pl.notify(pl._("Battle menu:"))
    if self.attackable:
      pl.notify(pl._("a: Attack."))
    if self.activatable:
      pl.notify(pl._("c: activate."))
    if self.to_m2:
      pl.notify(pl._("m: Main phase 2."))
    if self.to_ep:
      pl.notify(pl._("e: End phase."))
    def r(caller):
      if caller.text == 'a' and self.attackable:
        self.battle_attack(caller.connection)
      elif caller.text == 'c' and self.activatable:
        self.battle_activate(caller.connection)
      elif caller.text == 'e' and self.to_ep:
        self.set_responsei(3)
        reactor.callLater(0, procduel, self)
      elif caller.text == 'm' and self.to_m2:
        self.set_responsei(2)
        reactor.callLater(0, procduel, self)
      else:
        pl.notify("Invalid option.")
        return self.display_battle_menu(pl)
    pl.notify(DuelReader, r, no_abort=pl._("Invalid command."), prompt=pl._("Select an option:"), restore_parser=duel_parser)

  def battle_attack(self, con):
    pl = self.players[con.duel_player]
    pln = con.duel_player
    pl.notify(pl._("Select card to attack with:"))
    specs = {}
    for c in self.attackable:
      spec = self.card_to_spec(pln, c)
      pl.notify("%s: %s (%d/%d)" % (spec, c.get_name(pl), c.attack, c.defense))
      specs[spec] = c
    pl.notify(pl._("z: back."))
    def r(caller):
      if caller.text == 'z':
        self.display_battle_menu(pl)
        return
      if caller.text not in specs:
        pl.notify(pl._("Invalid cardspec. Retry."))
        return self.battle_attack(pl)
      card = specs[caller.text]
      seq = self.attackable.index(card)
      self.set_responsei((seq << 16) + 1)
      reactor.callLater(0, procduel, self)
    pl.notify(DuelReader, r, no_abort=pl._("Invalid command."), prompt=pl._("Select a card:"), restore_parser=duel_parser)

  def battle_activate(self, con):
    pl = self.players[con.duel_player]
    pln = con.duel_player
    pl.notify(pl._("Select card to activate:"))
    specs = {}
    for c in self.activatable:
      spec = self.card_to_spec(pln, c)
      pl.notify("%s: %s (%d/%d)" % (spec, c.get_name(pl), c.attack, c.defense))
      specs[spec] = c
    pl.notify(pl._("z: back."))
    def r(caller):
      if caller.text == 'z':
        self.display_battle_menu(pl)
        return
      if caller.text not in specs:
        pl.notify(pl._("Invalid cardspec. Retry."))
        pl.notify(DuelReader, r, no_abort="Invalid command", restore_parser=duel_parser)
        return
      card = specs[caller.text]
      seq = self.activatable.index(card)
      self.set_responsei((seq << 16))
      reactor.callLater(0, procduel, self)
    pl.notify(DuelReader, r, no_abort="Invalid command", restore_parser=duel_parser)

  def card_to_spec(self, player, card):
    s = ""
    if card.controller != player:
      s += "o"
    if card.location == dm.LOCATION_HAND:
      s += "h"
    elif card.location == dm.LOCATION_MZONE:
      s += "m"
    elif card.location == dm.LOCATION_SZONE:
      s += "s"
    elif card.location == dm.LOCATION_GRAVE:
      s += "g"
    elif card.location == dm.LOCATION_EXTRA:
      s += "x"
    elif card.location == dm.LOCATION_REMOVED:
      s += "r"
    s += str(card.sequence + 1)
    return s

  def attack(self, ac, al, aseq, apos, tc, tl, tseq, tpos):
    acard = self.get_card(ac, al, aseq)
    if not acard:
      return
    name = self.players[ac].nickname
    if tc == 0 and tl == 0 and tseq == 0 and tpos == 0:
      for pl in self.players + self.watchers:
        aspec = self.card_to_spec(pl.duel_player, acard)
        pl.notify(pl._("%s prepares to attack with %s (%s)") % (name, aspec, acard.get_name(pl)))
      return
    tcard = self.get_card(tc, tl, tseq)
    if not tcard:
      return
    for pl in self.players + self.watchers:
      aspec = self.card_to_spec(pl.duel_player, acard)
      tspec = self.card_to_spec(pl.duel_player, tcard)
      tcname = tcard.get_name(pl)
      if (tcard.controller != pl.duel_player or pl.watching) and tcard.position in (0x8, 0xa):
        tcname = pl._("%s card") % tcard.get_position(pl)
      pl.notify(pl._("%s prepares to attack %s (%s) with %s (%s)") % (name, tspec, tcname, aspec, acard.get_name(pl)))

  def begin_damage(self):
    for pl in self.players + self.watchers:
      pl.notify(pl._("begin damage"))

  def end_damage(self):
    for pl in self.players + self.watchers:
      pl.notify(pl._("end damage"))

  def battle(self, attacker, aa, ad, bd0, tloc, da, dd, bd1):
    loc = (attacker >> 8) & 0xff
    seq = (attacker >> 16) & 0xff
    c2 = attacker & 0xff
    card = self.get_card(c2, loc, seq)
    tc = tloc & 0xff
    tl = (tloc >> 8) & 0xff
    tseq = (tloc >> 16) & 0xff
    if tloc:
      target = self.get_card(tc, tl, tseq)
    else:
      target = None
    for pl in self.players + self.watchers:
      if target:
        pl.notify(pl._("%s (%d/%d) attacks %s (%d/%d)") % (card.get_name(pl), aa, ad, target.get_name(pl), da, dd))
      else:
        pl.notify(pl._("%s (%d/%d) attacks") % (card.get_name(pl), aa, ad))

  def damage(self, player, amount):
    new_lp = self.lp[player]-amount
    pl = self.players[player]
    op = self.players[1 - player]
    pl.notify(pl._("Your lp decreased by %d, now %d") % (amount, new_lp))
    op.notify(op._("%s's lp decreased by %d, now %d") % (self.players[player].nickname, amount, new_lp))
    for pl in self.watchers:
      pl.notify(pl._("%s's lp decreased by %d, now %d") % (self.players[player].nickname, amount, new_lp))
    self.lp[player] -= amount

  def recover(self, player, amount):
    new_lp = self.lp[player] + amount
    pl = self.players[player]
    op = self.players[1 - player]
    pl.notify(pl._("Your lp increased by %d, now %d") % (amount, new_lp))
    op.notify(op._("%s's lp increased by %d, now %d") % (self.players[player].nickname, amount, new_lp))
    for pl in self.watchers:
      pl.notify(pl._("%s's lp increased by %d, now %d") % (self.players[player].nickname, amount, new_lp))
    self.lp[player] += amount

  def notify_all(self, s):
    for pl in self.players:
      pl.notify(s)

  def hint(self, msg, player, data):
    pl = self.players[player]
    op = self.players[1 - player]
    if msg == 3 and data in strings[pl.language]['system']:
      self.players[player].notify(strings[pl.language]['system'][data])
    elif msg == 6 or msg == 7 or msg == 8:
      reactor.callLater(0, procduel, self)
    elif msg == 9:
      op.notify(strings[op.language]['system'][1512] % data)
      reactor.callLater(0, procduel, self)

  def select_card(self, player, cancelable, min_cards, max_cards, cards, is_tribute=False):
    con = self.players[player]
    con.card_list = cards
    def prompt():
      if is_tribute:
        con.notify(con._("Select %d to %d cards to tribute separated by spaces:") % (min_cards, max_cards))
      else:
        con.notify(con._("Select %d to %d cards separated by spaces:") % (min_cards, max_cards))
      for i, c in enumerate(cards):
        name = self.cardlist_info_for_player(c, con)
        con.notify("%d: %s" % (i+1, name))
      con.notify(DuelReader, f, no_abort="Invalid command", restore_parser=duel_parser)
    def error(text):
      con.notify(text)
      return prompt()
    def f(caller):
      cds = [i - 1 for i in self.parse_ints(caller.text)]
      if len(cds) != len(set(cds)):
        return error(con._("Duplicate values not allowed."))
      if (not is_tribute and len(cds) < min_cards) or len(cds) > max_cards:
        return error(con._("Please enter between %d and %d cards.") % (min_cards, max_cards))
      if cds and (min(cds) < 0 or max(cds) > len(cards) - 1):
        return error(con._("Invalid value."))
      buf = bytes([len(cds)])
      tribute_value = 0
      for i in cds:
        tribute_value += (cards[i].release_param if is_tribute else 0)
        buf += bytes([i])
      if is_tribute and tribute_value < min_cards:
        return error(con._("Not enough tributes."))
      self.set_responseb(buf)
      reactor.callLater(0, procduel, self)
    return prompt()

  def cardlist_info_for_player(self, card, con):
    spec = self.card_to_spec(con.duel_player, card)
    if card.location == dm.LOCATION_DECK:
      spec = con._("deck")
    cls = (card.controller, card.location, card.sequence)
    if card.controller != con.duel_player and card.position in (0x8, 0xa) and cls not in self.revealed:
      position = card.get_position(con)
      return (con._("{position} card ({spec})")
        .format(position=position, spec=spec))
    name = card.get_name(con)
    return "{name} ({spec})".format(name=name, spec=spec)

  def show_table(self, con, player, hide_facedown=False):
    mz = self.get_cards_in_location(player, dm.LOCATION_MZONE)
    sz = self.get_cards_in_location(player, dm.LOCATION_SZONE)
    if len(mz+sz) == 0:
      con.notify(con._("Table is empty."))
      return
    for card in mz:
      s = "m%d: " % (card.sequence + 1)
      if hide_facedown and card.position in (0x8, 0xa):
        s += card.get_position(con)
      else:
        s += card.get_name(con) + " "
        s += (con._("({attack}/{defense}) level {level}")
          .format(attack=card.attack, defense=card.defense, level=card.level))
        s += " " + card.get_position(con)

        if len(card.xyz_materials):
          s += " ("+con._("xyz materials: %d")%(len(card.xyz_materials))+")"
        counters = []
        for c in card.counters:
          counter_type = c & 0xffff
          counter_val = (c >> 16) & 0xffff
          counter_type = strings[con.language]['counter'][counter_type]
          counter_str = "%s: %d" % (counter_type, counter_val)
          counters.append(counter_str)
        if counters:
          s += " (" + ", ".join(counters) + ")"
      con.notify(s)
    for card in sz:
      s = "s%d: " % (card.sequence + 1)
      if hide_facedown and card.position in (0x8, 0xa):
        s += card.get_position(con)
      else:
        s += card.get_name(con) + " "
        s += card.get_position(con)

        if card.equip_target:

          s += ' ' + con._('(equipped to %s)')%(self.card_to_spec(con.duel_player, card.equip_target))

        counters = []
        for c in card.counters:
          counter_type = c & 0xffff
          counter_val = (c >> 16) & 0xffff
          counter_type = strings[con.language]['counter'][counter_type]
          counter_str = "%s: %d" % (counter_type, counter_val)
          counters.append(counter_str)
        if counters:
          s += " (" + ", ".join(counters) + ")"

      con.notify(s)

  def show_cards_in_location(self, con, player, location, hide_facedown=False):
    cards = self.get_cards_in_location(player, location)
    if not cards:
      con.notify(con._("Table is empty."))
      return
    for card in cards:
      s = self.card_to_spec(player, card) + " "
      if hide_facedown and card.position in (0x8, 0xa):
        s += card.get_position(con)
      else:
        s += card.get_name(con) + " "
        s += card.get_position(con)
        if card.type & dm.TYPE_MONSTER:
          s += " " + con._("level %d") % card.level
      con.notify(s)

  def show_hand(self, con, player):
    h = self.get_cards_in_location(player, dm.LOCATION_HAND)
    if not h:
      con.notify(con._("Your hand is empty."))
      return
    for c in h:
      con.notify("h%d: %s" % (c.sequence + 1, c.get_name(con)))

  def show_score(self, con):
    player = con.duel_player
    duel = con.duel
    deck = duel.get_cards_in_location(player, dm.LOCATION_DECK)
    odeck = duel.get_cards_in_location(1 - player, dm.LOCATION_DECK)
    grave = duel.get_cards_in_location(player, dm.LOCATION_GRAVE)
    ograve = duel.get_cards_in_location(1 - player, dm.LOCATION_GRAVE)
    hand = duel.get_cards_in_location(player, dm.LOCATION_HAND)
    ohand = duel.get_cards_in_location(1 - player, dm.LOCATION_HAND)
    removed = duel.get_cards_in_location(player, dm.LOCATION_REMOVED)
    oremoved = duel.get_cards_in_location(1 - player, dm.LOCATION_REMOVED)
    if con.watching:
      nick0 = duel.players[0].nickname
      nick1 = duel.players[1].nickname
      con.notify(con._("LP: %s: %d %s: %d") % (nick0, duel.lp[player], nick1, duel.lp[1 - player]))
      con.notify(con._("Hand: %s: %d %s: %d") % (nick0, len(hand), nick1, len(ohand)))
      con.notify(con._("Deck: %s: %d %s: %d") % (nick0, len(deck), nick1, len(odeck)))
      con.notify(con._("Grave: %s: %d %s: %d") % (nick0, len(grave), nick1, len(ograve)))
      con.notify(con._("Removed: %s: %d %s: %d") % (nick0, len(removed), nick1, len(oremoved)))
    else:
      con.notify(con._("Your LP: %d Opponent LP: %d") % (duel.lp[player], duel.lp[1 - player]))
      con.notify(con._("Hand: You: %d Opponent: %d") % (len(hand), len(ohand)))
      con.notify(con._("Deck: You: %d Opponent: %d") % (len(deck), len(odeck)))
      con.notify(con._("Grave: You: %d Opponent: %d") % (len(grave), len(ograve)))
      con.notify(con._("Removed: You: %d Opponent: %d") % (len(removed), len(oremoved)))

  def move(self, code, location, newloc, reason):
    card = dm.Card.from_code(code)
    card.set_location(location)
    pl = self.players[card.controller]
    op = self.players[1 - card.controller]
    plspec = self.card_to_spec(pl.duel_player, card)
    ploc = (location >> 8) & 0xff
    pnewloc = (newloc >> 8) & 0xff
    if reason & 0x01 and ploc != pnewloc:
      pl.notify(pl._("Card %s (%s) destroyed.") % (plspec, card.get_name(pl)))
      for w in self.watchers+[op]:
        s = self.card_to_spec(w.duel_player, card)
        w.notify(w._("Card %s (%s) destroyed.") % (s, card.get_name(w)))
    elif ploc == pnewloc and ploc in (dm.LOCATION_MZONE, dm.LOCATION_SZONE):
      cnew = dm.Card.from_code(code)
      cnew.set_location(newloc)

      if (location & 0xff) != (newloc & 0xff):
        # controller changed too (e.g. change of heart)
        pl.notify(pl._("your card {spec} ({name}) changed controller to {op} and is now located at {targetspec}.").format(spec=plspec, name = card.get_name(pl), op = op.nickname, targetspec = self.card_to_spec(pl.duel_player, cnew)))
        op.notify(op._("you now control {plname}s card {spec} ({name}) and its located at {targetspec}.").format(plname=pl.nickname, spec=self.card_to_spec(op.duel_player, card), name = card.get_name(op), targetspec = self.card_to_spec(op.duel_player, cnew)))
        for w in self.watchers:
          s = self.card_to_spec(w.duel_player, card)
          ts = self.card_to_spec(w.duel_player, cnew)
          w.notify(w._("{plname}s card {spec} ({name}) changed controller to {op} and is now located at {targetspec}.").format(plname=pl.nickname, op=op.nickname, spec=s, targetspec=ts, name=card.get_name(w)))
      else:
        # only place changed (alien decks e.g.)
        pl.notify(pl._("your card {spec} ({name}) switched its zone to {targetspec}.").format(spec=plspec, name=card.get_name(pl), targetspec=self.card_to_spec(pl.duel_player, cnew)))
        for w in self.watchers+[op]:
          s = self.card_to_spec(w.duel_player, card)
          ts = self.card_to_spec(w.duel_player, cnew)
          w.notify(w._("{plname}s card {spec} ({name}) changed its zone to {targetspec}.").format(plname=pl.nickname, spec=s, targetspec=ts, name=card.get_name(w)))
    elif reason & 0x4000 and ploc != pnewloc:
      pl.notify(pl._("you discarded {spec} ({name}).").format(spec = plspec, name = card.get_name(pl)))
      for w in self.watchers+[op]:
        s = self.card_to_spec(w.duel_player, card)
        w.notify(w._("{plname} discarded {spec} ({name}).").format(plname=pl.nickname, spec=s, name=card.get_name(w)))
    elif ploc == dm.LOCATION_REMOVED and pnewloc in (dm.LOCATION_SZONE, dm.LOCATION_MZONE):
      cnew = dm.Card.from_code(code)
      cnew.set_location(newloc)
      pl.notify(pl._("your banished card {spec} ({name}) returns to the field at {targetspec}.").format(spec=plspec, name=card.get_name(pl), targetspec=self.card_to_spec(pl.duel_player, cnew)))
      for w in self.watchers+[op]:
        s=self.card_to_spec(w.duel_player, card)
        ts = self.card_to_spec(w.duel_player, cnew)
        w.notify(w._("{plname}'s banished card {spec} ({name}) returned to their field at {targetspec}.").format(plname=pl.nickname, spec=s, targetspec=ts, name=card.get_name(w)))
    elif ploc == dm.LOCATION_GRAVE and pnewloc in (dm.LOCATION_SZONE, dm.LOCATION_MZONE):
      cnew = dm.Card.from_code(code)
      cnew.set_location(newloc)
      pl.notify(pl._("your card {spec} ({name}) returns from the graveyard to the field at {targetspec}.").format(spec=plspec, name=card.get_name(pl), targetspec=self.card_to_spec(pl.duel_player, cnew)))
      for w in self.watchers+[op]:
        s = self.card_to_spec(w.duel_player, card)
        ts = self.card_to_spec(w.duel_player, cnew)
        w.notify(w._("{plname}s card {spec} ({name}) returns from the graveyard to the field at {targetspec}.").format(plname = pl.nickname, spec=s, targetspec=ts, name = card.get_name(w)))
    elif pnewloc == dm.LOCATION_HAND and ploc != pnewloc:
      pl.notify(pl._("Card {spec} ({name}) returned to hand.")
        .format(spec=plspec, name=card.get_name(pl)))
      for w in self.watchers+[op]:
        if card.position in (dm.POS_FACEDOWN_DEFENSE, dm.POS_FACEDOWN):
          name = w._("Face-down card")
        else:
          name = card.get_name(w)
        s = self.card_to_spec(w.duel_player, card)
        w.notify(w._("{plname}'s card {spec} ({name}) returned to their hand.")
          .format(plname=pl.nickname, spec=s, name=name))
    elif reason & 0x12 and ploc != pnewloc:
      pl.notify(pl._("You tribute {spec} ({name}).")
        .format(spec=plspec, name=card.get_name(pl)))
      for w in self.watchers+[op]:
        s = self.card_to_spec(w.duel_player, card)
        if card.position in (dm.POS_FACEDOWN_DEFENSE, dm.POS_FACEDOWN):
          name = w._("%s card") % card.get_position(w)
        else:
          name = card.get_name(w)
        w.notify(w._("{plname} tributes {spec} ({name}).")
          .format(plname=pl.nickname, spec=s, name=name))
    elif ploc == dm.LOCATION_OVERLAY+dm.LOCATION_MZONE and pnewloc in (dm.LOCATION_GRAVE, dm.LOCATION_REMOVED):
      pl.notify(pl._("you detached %s.")%(card.get_name(pl)))
      for w in self.watchers+[op]:
        w.notify(w._("%s detached %s")%(pl.nickname, card.get_name(w)))
    elif ploc != pnewloc and pnewloc == dm.LOCATION_GRAVE:
      pl.notify(pl._("your card {spec} ({name}) was sent to the graveyard.").format(spec=plspec, name=card.get_name(pl)))
      for w in self.watchers+[op]:
        s = self.card_to_spec(w.duel_player, card)
        w.notify(w._("{plname}'s card {spec} ({name}) was sent to the graveyard.").format(plname=pl.nickname, spec=s, name=card.get_name(w)))
    elif ploc != pnewloc and pnewloc == dm.LOCATION_REMOVED:
      pl.notify(pl._("your card {spec} ({name}) was banished.").format(spec=plspec, name=card.get_name(pl)))
      for w in self.watchers+[op]:
        if card.position in (dm.POS_FACEDOWN_DEFENSE, dm.POS_FACEDOWN):
          name = w._("Face-down defense")
        else:
          name = card.get_name(w)
        s = self.card_to_spec(w.duel_player, card)
        w.notify(w._("{plname}'s card {spec} ({name}) was banished.").format(plname=pl.nickname, spec=s, name=name))
    elif ploc != pnewloc and pnewloc == dm.LOCATION_DECK:
      pl.notify(pl._("your card {spec} ({name}) returned to your deck.").format(spec=plspec, name=card.get_name(pl)))
      for w in self.watchers+[op]:
        s = self.card_to_spec(w.duel_player, card)
        w.notify(w._("{plname}'s card {spec} ({name}) returned to their deck.").format(plname=pl.nickname, spec=s, name=card.get_name(w)))
    elif ploc != pnewloc and pnewloc == dm.LOCATION_EXTRA:
      pl.notify(pl._("your card {spec} ({name}) returned to your extra deck.").format(spec=plspec, name=card.get_name(pl)))
      for w in self.watchers+[op]:
        s = self.card_to_spec(w.duel_player, card)
        w.notify(w._("{plname}'s card {spec} ({name}) returned to their extra deck.").format(plname=pl.nickname, spec=s, name=card.get_name(w)))

  def show_info(self, card, pl):
    pln = pl.duel_player
    cs = self.card_to_spec(pln, card)
    if card.position in (0x8, 0xa) and (pl.watching or card in self.get_cards_in_location(1 - pln, dm.LOCATION_MZONE) + self.get_cards_in_location(1 - pln, dm.LOCATION_SZONE)):
      pl.notify(pl._("%s: %s card.") % (cs, card.get_position(pl)))
      return
    pl.notify(card.get_info(pl))

  def show_info_cmd(self, con, spec):
    cards = []
    for i in (0, 1):
      for j in (dm.LOCATION_MZONE, dm.LOCATION_SZONE, dm.LOCATION_GRAVE, dm.LOCATION_REMOVED, dm.LOCATION_HAND, dm.LOCATION_EXTRA):
        cards.extend(card for card in self.get_cards_in_location(i, j) if card.controller == con.duel_player or card.position not in (0x8, 0xa))
    specs = {}
    for card in cards:
      specs[self.card_to_spec(con.duel_player, card)] = card
    for i, card in enumerate(con.card_list):
      specs[str(i + 1)] = card
    if spec not in specs:
      con.notify(con._("Invalid card."))
      return
    self.show_info(specs[spec], con)

  def pos_change(self, card, prevpos):
    cs = self.card_to_spec(card.controller, card)
    cso = self.card_to_spec(1 - card.controller, card)
    cpl = self.players[card.controller]
    op = self.players[1 - card.controller]
    cpl.notify(cpl._("The position of card %s (%s) was changed to %s.") % (cs, card.get_name(cpl), card.get_position(cpl)))
    op.notify(op._("The position of card %s (%s) was changed to %s.") % (cso, card.get_name(op), card.get_position(op)))
    for w in self.watchers:
      cs = self.card_to_spec(w.duel_player, card)
      w.notify(w._("The position of card %s (%s) was changed to %s.") % (cs, card.get_name(w), card.get_position(w)))

  def set(self, card):
    c = card.controller
    cpl = self.players[c]
    opl = self.players[1 - c]
    cpl.notify(cpl._("You set %s (%s) in %s position.") %
    (self.card_to_spec(c, card), card.get_name(cpl), card.get_position(cpl)))
    op = 1 - c
    on = self.players[c].nickname
    opl.notify(opl._("%s sets %s in %s position.") %
    (on, self.card_to_spec(op, card), card.get_position(opl)))
    for pl in self.watchers:
      pl.notify(pl._("%s sets %s in %s position.") %
      (on, self.card_to_spec(pl, card), card.get_position(pl)))

  def chaining(self, card, tc, tl, ts, desc, cs):
    c = card.controller
    o = 1 - c
    n = self.players[c].nickname
    self.chaining_player = c
    if card.type & 0x2:
      if self.players[c].soundpack:
        self.players[c].notify("### activate_spell")
      if self.players[o].soundpack:
        self.players[o].notify("### activate_spell")
    elif card.type & 0x4:
      if self.players[c].soundpack:
        self.players[c].notify("### activate_trap")
      if self.players[o].soundpack:
        self.players[o].notify("### activate_trap")

    self.players[c].notify(self.players[c]._("Activating %s") % card.get_name(self.players[c]))
    self.players[o].notify(self.players[o]._("%s activating %s") % (n, card.get_name(self.players[o])))
    for pl in self.watchers:
      if card.type & 0x2:
        if pl.soundpack:
          pl.notify("### activate_spell")
      if card.type & 0x4:
        if pl.soundpack:
          pl.notify("### activate_trap")
      pl.notify(pl._("%s activating %s") % (n, card.get_name(pl)))

  def select_position(self, player, card, positions):
    pl = self.players[player]
    m = Menu(pl._("Select position for %s:") % (card.get_name(pl),), no_abort="Invalid option.", persistent=True, restore_parser=duel_parser)
    def set(caller, pos=None):
      self.set_responsei(pos)
      reactor.callLater(0, procduel, self)
    if positions & 1:
      m.item(pl._("Face-up attack"))(lambda caller: set(caller, 1))
    if positions & 2:
      m.item(pl._("Face-down attack"))(lambda caller: set(caller, 2))
    if positions & 4:
      m.item(pl._("Face-up defense"))(lambda caller: set(caller, 4))
    if positions & 8:
      m.item(pl._("Face-down defense"))(lambda caller: set(caller, 8))
    pl.notify(m)

  def yesno(self, player, desc):
    pl = self.players[player]
    old_parser = pl.parser
    def yes(caller):
      self.set_responsei(1)
      reactor.callLater(0, procduel, self)
    def no(caller):
      self.set_responsei(0)
      reactor.callLater(0, procduel, self)
    if desc > 10000:
      code = desc >> 4
      opt = dm.Card.from_code(code).strings[desc & 0xf]
    else:
      opt = "String %d" % desc
      opt = strings[pl.language]['system'].get(desc, opt)
    pl.notify(YesOrNo, opt, yes, no=no, restore_parser=old_parser)

  def select_effectyn(self, player, card, desc):
    pl = self.players[player]
    old_parser = pl.parser
    def yes(caller):
      self.set_responsei(1)
      reactor.callLater(0, procduel, self)
    def no(caller):
      self.set_responsei(0)
      reactor.callLater(0, procduel, self)
    spec = self.card_to_spec(player, card)
    question = pl._("Do you want to use the effect from {card} in {spec}?").format(card=card.get_name(pl), spec=spec)
    s = card.get_effect_description(pl, desc, True)
    if s != '':
      question += '\n'+s
    pl.notify(YesOrNo, question, yes, no=no, restore_parser=old_parser)

  def win(self, player, reason):
    if player == 2:
      self.notify_all("The duel was a draw.")
      self.end()
      return
    winner = self.players[player]
    loser = self.players[1 - player]
    reason_str = strings[winner.language]['victory'][reason]
    winner.notify(winner._("You won (%s).") % reason_str)
    reason_str = strings[loser.language]['victory'][reason]
    loser.notify(loser._("You lost (%s).") % reason_str)
    for pl in self.watchers:
      reason_str = strings[pl.language]['victory'][reason]
      pl.notify(pl._("%s won (%s).") % (winner.nickname, reason_str))
    if not self.private:
      for pl in game.players.values():
        reason_str = strings[pl.language]['victory'][reason]
        announce_challenge(pl, pl._("%s won the duel between %s and %s (%s).") % (winner.nickname, self.players[0].nickname, self.players[1].nickname, reason_str))
    self.end()

  def pay_lpcost(self, player, cost):
    self.lp[player] -= cost
    self.players[player].notify(self.players[player]._("You pay %d LP. Your LP is now %d.") % (cost, self.lp[player]))
    players = [self.players[1 - player]]
    players.extend(self.watchers)
    for pl in players:
      pl.notify(pl._("%s pays %d LP. Their LP is now %d.") % (self.players[player].nickname, cost, self.lp[player]))

  def sort_chain(self, player, cards):
    self.set_responsei(-1)
    reactor.callLater(0, procduel, self)

  def announce_attrib(self, player, count, avail):
    attributes = ('Earth', 'Water', 'Fire', 'Wind', 'Light', 'Dark', 'Divine')
    attrmap = {k: (1<<i) for i, k in enumerate(attributes)}
    avail_attributes = {k: v for k, v in attrmap.items() if avail & v}
    avail_attributes_keys = avail_attributes.keys()
    avail_attributes_values = list(avail_attributes.values())
    pl = self.players[player]
    def prompt():
      pl.notify("Type %d attributes separated by spaces." % count)
      for i, attrib in enumerate(avail_attributes_keys):
        pl.notify("%d. %s" % (i + 1, attrib))
      pl.notify(DuelReader, r, no_abort="Invalid command", restore_parser=duel_parser)
    def r(caller):
      items = caller.text.split()
      ints = []
      try:
        ints = [int(i) for i in items]
      except ValueError:
        pass
      ints = [i for i in ints if i > 0 <= len(avail_attributes_keys)]
      ints = set(ints)
      if len(ints) != count:
        pl.notify("Invalid attributes.")
        return prompt()
      value = sum(avail_attributes_values[i - 1] for i in ints)
      self.set_responsei(value)
      reactor.callLater(0, procduel, self)
    return prompt()

  def announce_card(self, player, type):
    pl = self.players[player]
    def prompt():
      pl.notify(pl._("Enter the name of a card:"))
      return pl.notify(Reader, r, no_abort=pl._("Invalid command."), restore_parser=duel_parser)
    def error(text):
      pl.notify(text)
      return prompt()
    def r(caller):
      card = get_card_by_name(pl, caller.text)
      if card is None:
        return error(pl._("No results found."))
      if not card.type & type:
        return error(pl._("Wrong type."))
      self.set_responsei(card.code)
      reactor.callLater(0, procduel, self)
    prompt()

  def announce_number(self, player, opts):
    pl = self.players[player]
    str_opts = [str(i) for i in opts]
    def prompt():
      pl.notify(pl._("Select a number, one of: {opts}")
        .format(opts=", ".join(str_opts)))
      return pl.notify(DuelReader, r, no_abort=pl._("Invalid command."), restore_parser=duel_parser)
    def r(caller):
      ints = self.parse_ints(caller.text)
      if not ints or ints[0] not in opts:
        return prompt()
      self.set_responsei(opts.index(ints[0]))
      reactor.callLater(0, procduel, self)
    prompt()

  def announce_card_filter(self, player, options):
    pl = self.players[player]
    def prompt():
      pl.notify(pl._("Enter the name of a card:"))
      return pl.notify(Reader, r, no_abort=pl._("Invalid command."), restore_parser=duel_parser)
    def error(text):
      pl.notify(text)
      return prompt()
    def r(caller):
      card = get_card_by_name(pl, caller.text)
      if card is None:
        return error(pl._("No results found."))
      cd = dm.ffi.new('struct card_data *')
      dm.card_reader_callback(card.code, cd)
      if not dm.lib.declarable(cd, len(options), options):
        return error(pl._("Wrong type."))
      self.set_responsei(card.code)
      reactor.callLater(0, procduel, self)
    prompt()

  def select_sum(self, mode, player, val, select_min, select_max, must_select, select_some):
    pl = self.players[player]
    must_select_value = sum(c.param for c in must_select)
    def prompt():
      if mode == 0:
        pl.notify(pl._("Select cards with a total value of %d, seperated by spaces.") % (val - must_select_value))
      else:
        pl.notify(pl._("Select cards with a total value of at least %d, seperated by spaces.") % (val - must_select_value))
      for c in must_select:
        pl.notify("%s must be selected, automatically selected." % c.get_name(pl))
      for i, card in enumerate(select_some):
        pl.notify("%d: %s (%d)" % (i+1, card.get_name(pl), card.param & 0xffff))
      return pl.notify(DuelReader, r, no_abort="Invalid entry.", restore_parser=duel_parser)
    def error(t):
      pl.notify(t)
      return prompt()
    def r(caller):
      ints = [i - 1 for i in self.parse_ints(caller.text)]
      if len(ints) != len(set(ints)):
        return error(pl._("Duplicate values not allowed."))
      if any(i for i in ints if i < 1 or i > len(select_some) - 1):
        return error(pl._("Value out of range."))
      selected = [select_some[i] for i in ints]
      s = [select_some[i].param & 0xffff  for i in ints]
      if mode == 1 and (sum(s) < val or sum(s) - min(s) >= val):
        return error(pl._("Levels out of range."))
      if mode == 0 and not check_sum(selected, val - must_select_value):
        return error(pl._("Selected value does not equal %d.") % (val,))
      lst = [len(ints) + len(must_select)]
      lst.extend([0] * len(must_select))
      lst.extend(ints)
      b = bytes(lst)
      self.set_responseb(b)
      reactor.callLater(0, procduel, self)
    prompt()

  def select_counter(self, player, countertype, count, cards):
    pl = self.players[player]
    counter_str = strings[pl.language]['counter'][countertype]
    def prompt():
      pl.notify(pl._("Type new {counter} for {cards} cards, separated by spaces.")
        .format(counter=counter_str, cards=len(cards)))
      for c in cards:
        pl.notify("%s (%d)" % (c.get_name(pl), c.counter))
      pl.notify(DuelReader, r, no_abort="Invalid command", restore_parser=duel_parser)
    def error(text):
      pl.notify(text)
      return prompt()
    def r(caller):
      ints = self.parse_ints(caller.text)
      ints = [i & 0xffff for i in ints]
      if len(ints) != len(cards):
        return error(pl._("Please specify %d values.") % len(cards))
      if any(cards[i].counter < val for i, val in enumerate(ints)):
        return error(pl._("Values cannot be greater than counter."))
      if sum(ints) != count:
        return error(pl._("Please specify %d values with a sum of %d.") % (len(cards), count))
      bytes = struct.pack('h' * len(cards), *ints)
      self.set_responseb(bytes)
      reactor.callLater(0, procduel, self)
    prompt()

  def parse_ints(self, text):
    ints = []
    try:
      for i in text.split():
        ints.append(int(i))
    except ValueError:
      pass
    return ints

  def become_target(self, tc, tl, tseq):
    card = self.get_card(tc, tl, tseq)
    if not card:
      return
    name = self.players[self.chaining_player].nickname
    for pl in self.players + self.watchers:
      spec = self.card_to_spec(pl.duel_player, card)
      tcname = card.get_name(pl)
      if (pl.watching or card.controller != pl.duel_player) and card.position in (0x8, 0xa):
        tcname = pl._("%s card") % card.get_position(pl)
      pl.notify(pl._("%s targets %s (%s)") % (name, spec, tcname))

  def announce_race(self, player, count, avail):
    races = (
      "Warrior", "Spellcaster", "Fairy", "Fiend", "Zombie",
      "Machine", "Aqua", "Pyro", "Rock", "Wind Beast",
      "Plant", "Insect", "Thunder", "Dragon", "Beast",
      "Beast Warrior", "Dinosaur", "Fish", "Sea Serpent", "Reptile",
      "psycho", "Divine", "Creator god", "Wyrm", "Cybers",
    )
    racemap = {k: (1<<i) for i, k in enumerate(races)}
    avail_races = {k: v for k, v in racemap.items() if avail & v}
    pl = self.players[player]
    def prompt():
      pl.notify("Type %d races separated by spaces." % count)
      for i, s in enumerate(avail_races.keys()):
        pl.notify("%d: %s" % (i+1, s))
      pl.notify(DuelReader, r, no_abort="Invalid entry.", restore_parser=duel_parser)
    def error(text):
      pl.notify(text)
      pl.notify(DuelReader, r, no_abort="Invalid entry.", restore_parser=duel_parser)
    def r(caller):
      ints = []
      try:
        for i in caller.text.split():
          ints.append(int(i) - 1)
      except ValueError:
        return error("Invalid value.")
      if len(ints) != count:
        return error("%d items required." % count)
      if len(ints) != len(set(ints)):
        return error("Duplicate values not allowed.")
      if any(i > len(avail_races) - 1 or i < 0 for i in ints):
        return error("Invalid value.")
      result = 0
      for i in ints:
        result |= list(avail_races.values())[i]
      self.set_responsei(result)
      reactor.callLater(0, procduel, self)
    prompt()

  def sort_card(self, player, cards):
    pl = self.players[player]
    def prompt():
      pl.notify(pl._("Sort %d cards by entering numbers separated by spaces (c = cancel):") % len(cards))
      for i, c in enumerate(cards):
        pl.notify("%d: %s" % (i+1, c.get_name(pl)))
      return pl.notify(DuelReader, r, no_abort=pl._("Invalid command."), restore_parser=duel_parser)
    def error(text):
      pl.notify(text)
      return prompt()
    def r(caller):
      if caller.text == 'c':
        self.set_responseb(bytes([255]))
        reactor.callLater(0, procduel, self)
        return
      ints = [i - 1 for i in self.parse_ints(caller.text)]
      if len(ints) != len(cards):
        return error(pl._("Please enter %d values.") % len(cards))
      if len(ints) != len(set(ints)):
        return error(pl._("Duplicate values not allowed."))
      if any(i < 0 or i > len(cards) - 1 for i in ints):
        return error(pl._("Please enter values between 1 and %d.") % len(cards))
      self.set_responseb(bytes(ints))
      reactor.callLater(0, procduel, self)
    prompt()

  def field_disabled(self, locations):
    specs = self.flag_to_usable_cardspecs(locations, reverse=True)
    opspecs = []
    for spec in specs:
      if spec.startswith('o'):
        opspecs.append(spec[1:])
      else:
        opspecs.append('o'+spec)
    self.players[0].notify(self.players[0]._("Field locations %s are disabled.") % ", ".join(specs))
    self.players[1].notify(self.players[1]._("Field locations %s are disabled.") % ", ".join(opspecs))

  def toss_coin(self, player, options):
    players = []
    players.extend(self.players + self.watchers)
    for pl in players:
      s = strings[pl.language]['system'][1623] + " "
      opts = [strings[pl.language]['system'][60] if opt else strings[pl.language]['system'][61] for opt in options]
      s += ", ".join(opts)
      pl.notify(s)

  def toss_dice(self, player, options):
    opts = [str(opt) for opt in options]
    players = []
    players.extend(self.players + self.watchers)
    for pl in players:
      s = strings[pl.language]['system'][1624] + " "
      s += ", ".join(opts)
      pl.notify(s)

  def confirm_cards(self, player, cards):
    pl = self.players[player]
    op = self.players[1 - player]
    players = [pl] + self.watchers
    for pl in players:
      pl.notify(pl._("{player} shows you {count} cards.")
        .format(player=op.nickname, count=len(cards)))
      for i, c in enumerate(cards):
        pl.notify("%s: %s" % (i + 1, c.get_name(pl)))
        self.revealed[(c.controller, c.location, c.sequence)] = True

  def chain_solved(self, count):
    self.revealed = {}

  def equip(self, card, target):
    for pl in self.players + self.watchers:
      c = self.cardlist_info_for_player(card, pl)
      t = self.cardlist_info_for_player(target, pl)
      pl.notify(pl._("{card} equipped to {target}.")
        .format(card=c, target=t))

  def lpupdate(self, player, lp):
    if lp > self.lp[player]:
      self.recover(player, lp - self.lp[player])
    else:
      self.damage(player, self.lp[player] - lp)

  def flipsummoning(self, card):
    cpl = self.players[card.controller]
    players = self.players + self.watchers
    for pl in players:
      spec = self.card_to_spec(pl.duel_player, card)
      pl.notify(pl._("{player} flip summons {card} ({spec}).")
      .format(player=cpl.nickname, card=card.get_name(pl), spec=spec))

  def shuffle(self, player):
    pl = self.players[player]
    pl.notify(pl._("you shuffled your deck."))
    for pl in self.watchers+[self.players[1 - player]]:
      pl.notify(pl._("%s shuffled their deck.")%(self.players[player].nickname))

  def end(self):
    super(MyDuel, self).end()
    for pl in self.players + self.watchers:
      if pl is None:
        continue
      pl.duel = None
      pl.intercept = None
      op = pl.parser
      if isinstance(op, DuelReader):
        op.done = lambda caller: None
      pl.parser = parser
      pl.watching = False
      pl.card_list = []
    for pl in self.watchers:
      pl.notify(pl._("Watching stopped."))
    check_reboot()

  def start_debug(self):
    self.debug_mode = True
    lt = datetime.datetime.now().strftime('%Y-%m-%d_%H:%M:%S')
    fn = lt+"_"+self.players[0].nickname+"_"+self.players[1].nickname
    self.debug_fp = open(os.path.join('duels', fn), 'w')
    self.debug(event_type='start', player0=self.players[0].nickname, player1=self.players[1].nickname,
    deck0=self.cards[0], deck1=self.cards[1], seed=self.seed)

  def debug(self, **kwargs):
    if not self.debug_mode:
      return
    s = json.dumps(kwargs)
    self.debug_fp.write(s+'\n')
    self.debug_fp.flush()

  def player_disconnected(self, con):
    if any(pl is None for pl in self.players):
      self.end()
      return
    self.players[con.duel_player] = None
    duels[con.nickname] = weakref.ref(self)
    self.lost_parser = con.parser
    for pl in self.players + self.watchers:
      if pl is None:
        continue
      pl.notify(pl._("%s disconnected, the duel is paused.") % con.nickname)
    for pl in self.players:
      if pl is None:
        continue
      pl.paused_parser = pl.parser
      pl.parser = parser

def check_sum(cards, acc):
  if acc < 0:
    return False
  if not cards:
    return acc == 0
  l = cards[0].param
  l1 = l & 0xffff
  l2 = l >> 16
  nc = cards[1:]
  res1 = check_sum(nc, acc - l1)
  if l2 > 0:
    res2 = check_sum(nc, acc - l2)
  else:
    res2 = False
  return res1 or res2

class DuelReader(Reader):
  def handle_line(self, con, line):
    con.seen_waiting = False
    for s, c in duel_parser.command_substitutions.items():
      if line.startswith(s):
        line = c+" "+line[1:]
        break
    cmd, args = self.split(line)
    if cmd in duel_parser.commands:
      duel_parser.handle_line(con, line)
      con.notify(self, self.done)
    else:
      super().handle_line(con, line)

@parser.command(names=["chat"], args_regexp=r'(.*)')
def chat(caller):
  text = caller.args[0]
  if not text:
    caller.connection.chat = not caller.connection.chat
    if caller.connection.chat:
      caller.connection.notify("Chat on.")
    else:
      caller.connection.notify(caller.connection._("Chat off."))
    return
  if not caller.connection.chat:
    caller.connection.chat = True
    caller.connection.notify(caller.connection._("Chat on."))
  for pl in game.players.values():
    if pl.chat and caller.connection.nickname not in pl.ignores:
      pl.notify(pl._("%s chats: %s") % (caller.connection.nickname, caller.args[0]))

@parser.command(names=["say"], args_regexp=r'(.*)')
def say(caller):
  text = caller.args[0]
  if not text:
    caller.connection.say = not caller.connection.say
    if caller.connection.say:
      caller.connection.notify(caller.connection._("Say on."))
    else:
      caller.connection.notify(caller.connection._("Say off."))
    return
  if not caller.connection.say:
    caller.connection.say = True
    caller.connection.notify(caller.connection._("Say on."))
  if not caller.connection.duel:
    caller.connection.notify(caller.connection._("Not in a duel."))
    return
  for pl in caller.connection.duel.players + caller.connection.duel.watchers:
    if caller.connection.nickname not in pl.ignores and pl.say:
      pl.notify(pl._("%s says: %s") % (caller.connection.nickname, caller.args[0]))

@parser.command(names=['who'], args_regexp=r'(.*)')
def who(caller):
  filters = ["duel", "watch", "idle"]
  showing = ["duel", "watch", "idle"]
  who_output = []
  text = caller.args[0]
  if text:
    showing = []
    text = text.split()
    for s in text:
      if s in filters:
        showing.append(s)
      else:
        caller.connection.notify(caller.connection._("Invalid filter: %s") % s)
        return
  caller.connection.notify(caller.connection._("Online players:"))
  for pl in sorted(game.players.values(), key=lambda x: x.nickname):
    s = pl.nickname
    if pl.afk is True:
      s += " " + caller.connection._("[AFK]")
    if pl.watching and "watch" in showing:
      if pl.duel.players[0]:
        pl0 = pl.duel.players[0].nickname
      else:
        pl0 = caller.connection._("n/a")
      if pl.duel.players[1]:
        pl1 = pl.duel.players[1].nickname
      else:
        pl1 = caller.connection._("n/a")
      who_output.append(caller.connection._("%s (Watching duel with %s and %s)") %(s, pl0, pl1))
    elif pl.duel and "duel" in showing:
      other = None
      if pl.duel.players[0] is pl:
        other = pl.duel.players[1]
      else:
        other = pl.duel.players[0]
      if other is None:
        other = caller.connection._("n/a")
      else:
        other = other.nickname
      if pl.duel.private is True:
        who_output.append(caller.connection._("%s (privately dueling %s)") %(pl.nickname, other))
      else:
        who_output.append(caller.connection._("%s (dueling %s)") %(pl.nickname, other))
    elif not pl.duel and not pl.watching:
      if "idle" in showing:
        who_output.append(s)
  for pl in who_output:
    caller.connection.notify(pl)


@duel_parser.command(names=['sc', 'score'])
def score(caller):
  if not caller.connection.duel:
    caller.connection.notify(caller.connection._("Not in a duel."))
    return
  caller.connection.duel.show_score(caller.connection)

@duel_parser.command(names=['watchers'])
def show_watchers(caller):
  if caller.connection.duel.watchers==[]:
    caller.connection.notify(caller.connection._("No one is watching this duel."))
  else:
    caller.connection.notify(caller.connection._("People watching this duel:"))
    for pl in sorted(caller.connection.duel.watchers, key=lambda x: x.nickname):
      caller.connection.notify(pl.nickname)

@parser.command(names=['replay'], args_regexp=r'(.*)=(\d+)', allowed=lambda caller: caller.connection.is_admin)
def replay(caller):
  with open(os.path.join('duels', caller.args[0])) as fp:
    lines = [json.loads(line) for line in fp]
  limit = int(caller.args[1])
  for line in lines[:limit]:
    if line['event_type'] == 'start':
      player0 = get_player(line['player0'])
      player1 = get_player(line['player1'])
      if not player0 or not player1:
        caller.connection.notify("One of the players is not logged in.")
        return
      if player0.duel or player1.duel:
        caller.connection.notify("One of the players is in a duel.")
        return
      duel = MyDuel(line.get('seed', 0))
      duel.load_deck(0, line['deck0'], shuffle=False)
      duel.load_deck(1, line['deck1'], shuffle=False)
      duel.players = [player0, player1]
      player0.duel = duel
      player1.duel = duel
      player0.duel_player = 0
      player1.duel_player = 1
      duel.start()
    elif line['event_type'] == 'process':
      procduel_replay(duel)
    elif line['event_type'] == 'set_responsei':
      duel.set_responsei(line['response'])
    elif line['event_type'] == 'set_responseb':
      duel.set_responseb(line['response'].encode('latin1'))
  reactor.callLater(0, procduel, duel)

def procduel_replay(duel):
  res = dm.lib.process(duel.duel)
  l = dm.lib.get_message(duel.duel, dm.ffi.cast('byte *', duel.buf))
  data = dm.ffi.unpack(duel.buf, l)
  cb = duel.cm.callbacks
  duel.cm.callbacks = collections.defaultdict(list)
  def tp(t):
    duel.tp = t
  duel.cm.register_callback('new_turn', tp)
  def recover(player, amount):
    duel.lp[player] += amount
  def damage(player, amount):
    duel.lp[player] -= amount
  duel.cm.register_callback('recover', recover)
  duel.cm.register_callback('damage', damage)
  duel.process_messages(data)
  duel.cm.callbacks = cb
  return data

@duel_parser.command(names=['info'], args_regexp=r'(.*)')
def info(caller):
  caller.connection.duel.show_info_cmd(caller.connection, caller.args[0])

@parser.command(names=['help'], args_regexp=r'(.*)')
def help(caller):
  topic = caller.args[0]
  if not topic:
    topic = "start"
  topic = topic.replace('/', '_').strip()
  fn = os.path.join('help', topic)
  if not os.path.isfile(fn):
    caller.connection.notify("No help topic.")
    return
  with open(fn, encoding='utf-8') as fp:
    caller.connection.notify(fp.read().rstrip('\n'))

@parser.command(names=['quit'])
def quit(caller):
  caller.connection.notify("Goodbye.")
  server.disconnect(caller.connection)

@parser.command(names=['lookup'], args_regexp=r'(.*)')
def lookup(caller):
  name = caller.args[0]
  card = get_card_by_name(caller.connection, name)
  if not card:
    caller.connection.notify(caller.connection._("No results found."))
    return
  caller.connection.notify(card.get_info(caller.connection))

def get_card_by_name(con, name):
  r = re.compile(r'^(\d+)\.(.+)$')
  r = r.search(name)
  if r:
    n, name = int(r.group(1)), r.group(2)
  else:
    n = 1
  if n == 0:
    n = 1
  name = '%'+name+'%'
  rows = con.cdb.execute('select id from texts where name like ? limit ?', (name, n)).fetchall()
  if not rows:
    return
  nr = rows[min(n - 1, len(rows) - 1)]
  card = dm.Card.from_code(nr[0])
  return card

@parser.command(names=['echo'], args_regexp=r'(.*)')
def echo(caller):
  caller.connection.notify(caller.args[0])

@parser.command(names='passwd')
def passwd(caller):
  session = caller.connection.session
  account = caller.connection.account
  new_password = ""
  old_parser = caller.connection.parser
  def r(caller):
    if not account.check_password(caller.text):
      caller.connection.notify("Incorrect password.")
      session.commit()
      return
    caller.connection.notify(Reader, r2, prompt="New password:", no_abort="Invalid command.", restore_parser=old_parser)
  def r2(caller):
    nonlocal new_password
    new_password = caller.text
    if len(new_password) < 6:
      caller.connection.notify("Passwords must be at least 6 characters.")
      caller.connection.notify(Reader, r2, prompt="New password:", no_abort="Invalid command.", restore_parser=old_parser)
      return
    caller.connection.notify(Reader, r3, prompt="Confirm password:", no_abort="Invalid command.", restore_parser=old_parser)
  def r3(caller):
    if new_password != caller.text:
      caller.connection.notify("Passwords don't match.")
      session.commit()
      return
    account.set_password(caller.text)
    session.commit()
    caller.connection.notify("Password changed.")
  caller.connection.notify(Reader, r, prompt="Current password:", no_abort="Invalid command.", restore_parser=old_parser)

@parser.command(names=['language'], args_regexp=r'(.*)')
def language(caller):
  lang = caller.args[0]
  if lang not in ('english', 'german', 'japanese', 'spanish'):
    caller.connection.notify("Usage: language <english/german/japanese/spanish>")
    return
  if lang == 'english':
    i18n.set_language(caller.connection, 'en')
  elif lang == 'german':
    i18n.set_language(caller.connection, 'de')
  elif lang == 'japanese':
    i18n.set_language(caller.connection, 'ja')
  elif lang == 'spanish':
    i18n.set_language(caller.connection, 'es')
  caller.connection.account.language = caller.connection.language
  caller.connection.session.commit()
  caller.connection.notify(caller.connection._("Language set."))

@parser.command(args_regexp=r'(.*)')
def encoding(caller):
  if caller.connection.web:
    caller.connection.notify(caller.connection._("Encoding is not needed when using the web client."))
    return
  try:
    codec = codecs.lookup(caller.args[0])
    if not codec._is_text_encoding:
      raise LookupError
  except LookupError:
    caller.connection.notify(caller.connection._("Unknown encoding."))
    return
  caller.connection.encode_args = (caller.args[0], 'replace')
  caller.connection.decode_args = (caller.args[0], 'ignore')
  caller.connection.account.encoding = caller.args[0]
  caller.connection.session.commit()
  caller.connection.notify(caller.connection._("Encoding set."))

@parser.command(allowed=lambda caller: caller.connection.is_admin)
def restart_websockets(caller):
  if not game.websocket_server:
    caller.connection.notify("Websocket server not enabled.")
    return
  caller.connection.notify("Stopping server...")
  d = game.websocket_server.stopListening()
  def stopped(r):
    caller.connection.notify("Done, restarting.")
    start_websocket_server()
  d.addCallback(stopped)
  d.addErrback(log.err)

@parser.command(args_regexp=r'(.*)', allowed=lambda caller: caller.connection.is_admin)
def announce(caller):
  if not caller.args[0]:
    caller.connection.notify("Announce what?")
    return
  for pl in game.players.values():
    pl.notify(pl._("Announcement: %s") % caller.args[0])

@parser.command(args_regexp=r'(.*)')
def tell(caller):
  args = caller.args[0].split(None, 1)
  if len(args) != 2:
    caller.connection.notify(caller.connection._("Usage: tell <player> <message>"))
    return
  player = args[0]
  players = guess_players(player, caller.connection.nickname)
  if len(players) == 1:
    player = players[0]
  elif len(players) > 1:
    caller.connection.notify(caller.connection._("Multiple players match this name: %s")%(','.join([p.nickname for p in players])))
    return
  else:
    caller.connection.notify(caller.connection._("That player is not online."))
    return
  if caller.connection.nickname in player.ignores:
    caller.connection.notify(caller.connection._("%s is ignoring you.") % player.nickname)
    return
  caller.connection.notify(caller.connection._("You tell %s: %s") % (player.nickname, args[1]))
  if player.afk is True:
    caller.connection.notify(caller.connection._("%s is AFK and may not be paying attention.") %(player.nickname))
  player.notify(player._("%s tells you: %s") % (caller.connection.nickname, args[1]))
  player.reply_to = caller.connection.nickname

@parser.command(args_regexp=r'(.*)')
def reply(caller):
  if not caller.args[0]:
    caller.connection.notify(caller.connection._("Usage: reply <message>"))
    return
  if not caller.connection.reply_to:
    caller.connection.notify(caller.connection._("No one to reply to."))
    return
  player = get_player(caller.connection.reply_to)
  if not player:
    caller.connection.notify(caller.connection._("That player is not online."))
    return
  caller.connection.notify(caller.connection._("You reply to %s: %s") % (player.nickname, caller.args[0]))
  player.notify(player._("%s replies: %s") % (caller.connection.nickname, caller.args[0]))
  player.reply_to = caller.connection.nickname

@parser.command
def soundpack_on(caller):
  caller.connection.soundpack = True

@parser.command(args_regexp=r'(.*)')
def watch(caller):
  con = caller.connection
  nick = caller.args[0]
  if not nick:
    caller.connection.watchnotify = not caller.connection.watchnotify
    if caller.connection.watchnotify:
      caller.connection.notify(caller.connection._("Watch notification enabled."))
    else:
      caller.connection.notify(caller.connection._("Watch notification disabled."))
    return
  if nick == 'stop':
    if not con.watching:
      con.notify(con._("You aren't watching a duel."))
      return
    con.duel.watchers.remove(con)
    for pl in con.duel.players + con.duel.watchers:
      if pl is None:
        continue
      if pl.watchnotify:
        pl.notify(pl._("%s is no longer watching this duel.")%(con.nickname))
    con.duel = None
    con.watching = False
    con.notify(con._("Watching stopped."))
    return
  players = guess_players(nick, con.nickname)
  if con.duel:
    con.notify(con._("You are already in a duel."))
    return
  elif len(players) > 1:
    con.notify(con._("Multiple players match this name: %s")%(','.join([p.nickname for p in players])))
    return
  elif not len(players):
    con.notify(con._("That player is not online."))
    return
  elif not players[0].duel:
    con.notify(con._("That player is not in a duel."))
    return
  elif players[0].duel.private:
    con.notify(con._("That duel is private."))
    return
  player = players[0]
  con.duel = player.duel
  con.duel_player = 0
  con.duel.watchers.append(con)
  con.parser = duel_parser
  con.watching = True
  con.notify(con._("Watching duel between %s and %s.") % (con.duel.players[0].nickname, con.duel.players[1].nickname))
  for pl in con.duel.players + con.duel.watchers:
    if pl is None:
      continue
    if pl.watchnotify:
      pl.notify(pl._("%s is now watching this duel.")%(con.nickname))

@parser.command(args_regexp=r'(.*)')
def ignore(caller):
  con = caller.connection
  name = caller.args[0]
  if not name:
    con.notify("Ignored accounts:")
    for account in con.account.ignores:
      con.notify(account.ignored_account.name)
    con.session.commit()
    return
  name = name.capitalize()
  if name == con.nickname:
    con.notify(con._("You cannot ignore yourself."))
    return
  account = con.session.query(models.Account).filter_by(name=name).first()
  if not account:
    con.notify(con._("That account doesn't exist. Make sure you enter the full name (no auto-completion for security reasons)."))
    con.session.commit()
    return
  ignore = con.session.query(models.Ignore).filter_by(account_id=con.account.id, ignored_account_id=account.id).first()
  if not ignore:
    i = models.Ignore(account_id=con.account.id, ignored_account_id=account.id)
    con.account.ignores.append(i)
    con.session.add(i)
    con.notify(con._("Ignoring %s.") % name)
    con.ignores.add(name)
    con.session.commit()
    return
  else:
    con.session.delete(ignore)
    con.notify(con._("Stopped ignoring %s.") % name)
    con.ignores.discard(name)
    con.session.commit()

@parser.command
def challenge(caller):
  con = caller.connection
  con.challenge = not con.challenge
  if con.challenge:
    con.notify(con._("Challenge on."))
  else:
    con.notify(con._("Challenge off."))

@parser.command(allowed=lambda caller: caller.connection.is_admin)
def reboot(caller):
  game.rebooting = True
  check_reboot()

def check_reboot():
  duels = [c.duel for c in game.players.values()]
  if game.rebooting and not any(duels):
    for pl in game.players.values():
      pl.notify(pl._("Rebooting."))
    reactor.callLater(0.2, reactor.stop)

for key in parser.commands.keys():
  duel_parser.commands[key] = parser.commands[key]

if __name__ == '__main__':
  main()
