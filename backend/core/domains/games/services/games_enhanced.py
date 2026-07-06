"""Enhanced game service functions for streamlined workflows."""

from flask import request, jsonify, render_template
from flask_login import current_user
from extensions import db
from core.domains.games.models import GamePod, GamePodMember, GameRosterPlayer, GameRosterDeck, GameSession, GameSeat, GamePlayer, GameDeck, GameSeatAssignment
from core.shared.utils.time import utcnow
from core.domains.users.models import User
from sqlalchemy import func

from . import game_compat_service as legacy


def games_streamlined_players():
    """Streamlined pod management interface."""
    pods = (
        GamePod.query.filter(GamePod.owner_user_id == current_user.id)
        .order_by(func.lower(GamePod.name))
        .all()
    )

    pod_data = []
    for pod in pods:
        members = []
        for member in pod.members or []:
            if member.roster_player:
                rp = member.roster_player
                deck_count = len(rp.decks or [])
                archidekt_username = rp.archidekt_username or (
                    rp.user.archidekt_username if rp.user else None
                )
                members.append({
                    'member_id': member.id,
                    'roster_id': rp.id,
                    'label': rp.display_name or 'Player',
                    'deck_count': deck_count,
                    'archidekt_username': archidekt_username,
                })

        pod_data.append({
            'id': pod.id,
            'name': pod.name,
            'is_owner': True,
            'members': members,
        })

    return render_template("games/players_streamlined.html", pods=pod_data)


def games_quick_log():
    """Quick game logging interface."""
    pods = (
        GamePod.query.filter(GamePod.owner_user_id == current_user.id)
        .order_by(func.lower(GamePod.name))
        .all()
    )

    pod_data = []
    for pod in pods:
        members = []
        for member in pod.members or []:
            if member.roster_player:
                members.append({
                    'roster_id': member.roster_player.id,
                    'label': member.roster_player.display_name or 'Player',
                })

        pod_data.append({
            'id': pod.id,
            'name': pod.name,
            'members': members,
        })

    deck_options = (
        db.session.query(
            legacy.Folder.id,
            legacy.Folder.name,
            legacy.Folder.commander_name,
        )
        .join(legacy.FolderRole, legacy.FolderRole.folder_id == legacy.Folder.id)
        .filter(
            legacy.FolderRole.role.in_(legacy.FolderRole.DECK_ROLES),
            legacy.Folder.owner_user_id == current_user.id,
        )
        .order_by(func.lower(legacy.Folder.name))
        .limit(20)
        .all()
    )

    guest_deck_options = []
    for deck in deck_options:
        label = deck.name or f"Deck {deck.id}"
        if deck.commander_name:
            label = f"{label} · {deck.commander_name}"
        guest_deck_options.append({
            "id": deck.id,
            "label": label,
            "ref": f"folder:{deck.id}",
        })

    return render_template(
        "games/quick_log.html",
        pods=pod_data,
        guest_deck_options=guest_deck_options,
    )


def api_quick_pod_create():
    """API endpoint for quick pod creation."""
    try:
        data = request.get_json(silent=True) or {}
        pod_name = (data.get('pod_name') or '').strip()
        players_text = (data.get('players') or '').strip()
        auto_assign = bool(data.get('auto_assign_decks', False))

        if not pod_name:
            return jsonify({'error': 'Pod name is required'}), 400
        if not players_text:
            return jsonify({'error': 'At least one player is required'}), 400
        if len(pod_name) > 120:
            return jsonify({'error': 'Pod name must be 120 characters or fewer'}), 400

        existing = GamePod.query.filter_by(owner_user_id=current_user.id, name=pod_name).first()
        if existing:
            return jsonify({'error': 'A pod with that name already exists'}), 409

        pod = GamePod(owner_user_id=current_user.id, name=pod_name)
        db.session.add(pod)
        db.session.flush()

        player_names = [n.strip() for n in players_text.split('\n') if n.strip()]
        created_players = []

        for name in player_names[:4]:
            user = None
            if '@' in name:
                user = User.query.filter(func.lower(User.email) == name.lower()).first()
            else:
                user = User.query.filter(func.lower(User.username) == name.lower()).first()

            roster_player = GameRosterPlayer(
                owner_user_id=current_user.id,
                user_id=user.id if user else None,
                display_name=name,
            )
            db.session.add(roster_player)
            db.session.flush()

            db.session.add(GamePodMember(pod_id=pod.id, roster_player_id=roster_player.id))

            created_players.append({
                'name': name,
                'is_user': user is not None,
                'roster_id': roster_player.id,
            })

            if auto_assign and user:
                deck_ids = (
                    db.session.query(legacy.Folder.id)
                    .join(legacy.FolderRole, legacy.FolderRole.folder_id == legacy.Folder.id)
                    .filter(
                        legacy.FolderRole.role.in_(legacy.FolderRole.DECK_ROLES),
                        legacy.Folder.owner_user_id == user.id,
                    )
                    .limit(3)
                    .all()
                )
                for (deck_id,) in deck_ids:
                    db.session.add(GameRosterDeck(
                        roster_player_id=roster_player.id,
                        owner_user_id=current_user.id,
                        folder_id=deck_id,
                    ))

        db.session.commit()
        return jsonify({
            'success': True,
            'pod_id': pod.id,
            'pod_name': pod_name,
            'players': created_players,
            'message': f'Pod "{pod_name}" created with {len(created_players)} player(s)',
        })

    except Exception:
        db.session.rollback()
        from flask import current_app
        current_app.logger.exception("api_quick_pod_create failed")
        return jsonify({'error': 'Internal server error'}), 500


def api_auto_assign_decks():
    """API endpoint for automatic deck assignment."""
    try:
        data = request.get_json(silent=True) or {}
        pod_ids = data.get('pod_ids') or []

        if not pod_ids:
            return jsonify({'error': 'No pods specified'}), 400

        assignments_made = 0

        for pod_id in pod_ids:
            pod = GamePod.query.filter(
                GamePod.id == pod_id,
                GamePod.owner_user_id == current_user.id,
            ).first()
            if not pod:
                continue

            for member in pod.members or []:
                roster_player = member.roster_player
                if not roster_player:
                    continue

                existing_count = len(roster_player.decks or [])
                existing_folder_ids = {d.folder_id for d in (roster_player.decks or []) if d.folder_id}

                if roster_player.user_id:
                    target = 3
                    available = (
                        db.session.query(legacy.Folder.id)
                        .join(legacy.FolderRole, legacy.FolderRole.folder_id == legacy.Folder.id)
                        .filter(
                            legacy.FolderRole.role.in_(legacy.FolderRole.DECK_ROLES),
                            legacy.Folder.owner_user_id == roster_player.user_id,
                            legacy.Folder.id.notin_(existing_folder_ids),
                        )
                        .limit(max(0, target - existing_count))
                        .all()
                    )
                else:
                    target = 2
                    available = (
                        db.session.query(legacy.Folder.id)
                        .join(legacy.FolderRole, legacy.FolderRole.folder_id == legacy.Folder.id)
                        .filter(
                            legacy.FolderRole.role.in_(legacy.FolderRole.DECK_ROLES),
                            legacy.Folder.owner_user_id == current_user.id,
                            legacy.Folder.id.notin_(existing_folder_ids),
                        )
                        .limit(max(0, target - existing_count))
                        .all()
                    )

                for (deck_id,) in available:
                    db.session.add(GameRosterDeck(
                        roster_player_id=roster_player.id,
                        owner_user_id=current_user.id,
                        folder_id=deck_id,
                    ))
                    assignments_made += 1

        db.session.commit()
        return jsonify({
            'success': True,
            'assignments_made': assignments_made,
            'message': f'Auto-assigned {assignments_made} deck(s) across {len(pod_ids)} pod(s)',
        })

    except Exception:
        db.session.rollback()
        from flask import current_app
        current_app.logger.exception("api_auto_assign_decks failed")
        return jsonify({'error': 'Internal server error'}), 500


def api_quick_game_save():
    """API endpoint for saving quick game logs."""
    try:
        data = request.get_json(silent=True) or {}

        players = data.get('players') or {}
        decks = data.get('decks') or {}
        played_at_raw = data.get('played_at')
        winner_seat_num = data.get('winner_seat')
        win_via_combo = bool(data.get('win_via_combo', False))
        notes = (data.get('notes') or '').strip()

        if len(players) < 2:
            return jsonify({'error': 'At least 2 players are required'}), 400

        from datetime import datetime

        if played_at_raw:
            try:
                played_at = datetime.fromisoformat(played_at_raw)
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid played_at date format'}), 400
        else:
            played_at = utcnow()

        session = GameSession(
            owner_user_id=current_user.id,
            played_at=played_at,
            notes=notes or None,
            win_via_combo=win_via_combo,
        )
        db.session.add(session)
        db.session.flush()

        seats_by_number: dict[int, GameSeat] = {}

        for seat_num_str, player_data in players.items():
            seat_num = int(seat_num_str)

            seat = GameSeat(
                session_id=session.id,
                seat_number=seat_num,
                turn_order=seat_num,
            )
            db.session.add(seat)
            db.session.flush()
            seats_by_number[seat_num] = seat

            player = GamePlayer(
                user_id=player_data.get('user_id'),
                display_name=player_data.get('name'),
            )
            db.session.add(player)
            db.session.flush()

            deck_data = decks.get(seat_num_str) or {}
            deck = GameDeck(
                session_id=session.id,
                folder_id=deck_data.get('folder_id'),
                deck_name=deck_data.get('name') or 'Unknown Deck',
                commander_name=deck_data.get('commander'),
                commander_oracle_id=deck_data.get('oracle_id'),
            )
            db.session.add(deck)
            db.session.flush()

            assignment = GameSeatAssignment(
                session_id=session.id,
                seat_id=seat.id,
                player_id=player.id,
                deck_id=deck.id,
            )
            db.session.add(assignment)

        if winner_seat_num is not None:
            winner_seat = seats_by_number.get(int(winner_seat_num))
            if winner_seat:
                session.winner_seat_id = winner_seat.id

        db.session.commit()
        return jsonify({
            'success': True,
            'game_id': session.id,
            'message': 'Game logged successfully!',
        })

    except Exception:
        db.session.rollback()
        from flask import current_app
        current_app.logger.exception("api_quick_game_save failed")
        return jsonify({'error': 'Internal server error'}), 500


# Add these to the service exports
__all__ = [
    "games_streamlined_players",
    "games_quick_log", 
    "api_quick_pod_create",
    "api_auto_assign_decks",
    "api_quick_game_save"
]