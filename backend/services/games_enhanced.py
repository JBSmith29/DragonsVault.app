"""Enhanced game service functions for streamlined workflows."""

from flask import request, jsonify, render_template, flash, redirect, url_for
from flask_login import current_user
from extensions import db
from models import GamePod, GameRosterPlayer, GameRosterDeck, Folder, FolderRole, User
from sqlalchemy import func
import json


def games_streamlined_players():
    """Streamlined pod management interface."""
    # Get user's pods with enhanced data
    pods = (
        GamePod.query.filter(GamePod.owner_user_id == current_user.id)
        .order_by(func.lower(GamePod.name))
        .all()
    )
    
    # Enhanced pod data with member counts and deck assignments
    pod_data = []
    for pod in pods:
        members = []
        for member in pod.members or []:
            if member.roster_player:
                # Count assigned decks
                deck_count = len(member.roster_player.decks or [])
                members.append({
                    'member_id': member.id,
                    'roster_id': member.roster_player.id,
                    'label': member.roster_player.display_name or 'Player',
                    'deck_count': deck_count
                })
        
        pod_data.append({
            'id': pod.id,
            'name': pod.name,
            'is_owner': True,
            'members': members
        })
    
    return render_template(
        "games/players_streamlined.html",
        pods=pod_data
    )


def games_quick_log():
    """Quick game logging interface."""
    # Get user's pods for quick selection
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
                    'label': member.roster_player.display_name or 'Player'
                })
        
        pod_data.append({
            'id': pod.id,
            'name': pod.name,
            'members': members
        })
    
    # Get available decks for quick selection
    deck_options = (
        db.session.query(
            Folder.id,
            Folder.name,
            Folder.commander_name,
        )
        .join(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            FolderRole.role.in_(FolderRole.DECK_ROLES),
            Folder.owner_user_id == current_user.id
        )
        .order_by(func.lower(Folder.name))
        .limit(20)  # Limit for performance
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
            "ref": f"folder:{deck.id}"
        })
    
    return render_template(
        "games/quick_log.html",
        pods=pod_data,
        guest_deck_options=guest_deck_options
    )


def api_quick_pod_create():
    """API endpoint for quick pod creation."""
    if request.method != 'POST':
        return jsonify({'error': 'Method not allowed'}), 405
    
    try:
        data = request.get_json()
        pod_name = data.get('pod_name', '').strip()
        players_text = data.get('players', '').strip()
        auto_assign_decks = data.get('auto_assign_decks', False)
        
        if not pod_name or not players_text:
            return jsonify({'error': 'Pod name and players are required'}), 400
        
        # Create pod
        pod = GamePod(
            owner_user_id=current_user.id,
            name=pod_name
        )
        db.session.add(pod)
        db.session.flush()
        
        # Parse players (one per line)
        player_names = [name.strip() for name in players_text.split('\n') if name.strip()]
        created_players = []
        
        for name in player_names[:4]:  # Limit to 4 players
            # Check if it's an email/username of existing user
            user = None
            if '@' in name:
                user = User.query.filter(func.lower(User.email) == name.lower()).first()
            else:
                user = User.query.filter(func.lower(User.username) == name.lower()).first()
            
            # Create roster player
            roster_player = GameRosterPlayer(
                owner_user_id=current_user.id,
                user_id=user.id if user else None,
                display_name=name
            )
            db.session.add(roster_player)
            db.session.flush()
            
            # Add to pod
            from models import GamePodMember
            pod_member = GamePodMember(
                pod_id=pod.id,
                roster_player_id=roster_player.id
            )
            db.session.add(pod_member)
            
            created_players.append({
                'name': name,
                'is_user': user is not None,
                'roster_id': roster_player.id
            })
            
            # Auto-assign decks if requested and user exists
            if auto_assign_decks and user:
                user_decks = (
                    db.session.query(Folder.id)
                    .join(FolderRole, FolderRole.folder_id == Folder.id)
                    .filter(
                        FolderRole.role.in_(FolderRole.DECK_ROLES),
                        Folder.owner_user_id == user.id
                    )
                    .limit(3)  # Assign up to 3 decks
                    .all()
                )
                
                for (deck_id,) in user_decks:
                    deck_assignment = GameRosterDeck(
                        roster_player_id=roster_player.id,
                        owner_user_id=current_user.id,
                        folder_id=deck_id
                    )
                    db.session.add(deck_assignment)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'pod_id': pod.id,
            'pod_name': pod_name,
            'players': created_players,
            'message': f'Pod "{pod_name}" created with {len(created_players)} players'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


def api_auto_assign_decks():
    """API endpoint for automatic deck assignment."""
    if request.method != 'POST':
        return jsonify({'error': 'Method not allowed'}), 405
    
    try:
        data = request.get_json()
        pod_ids = data.get('pod_ids', [])
        
        if not pod_ids:
            return jsonify({'error': 'No pods specified'}), 400
        
        assignments_made = 0
        
        for pod_id in pod_ids:
            pod = GamePod.query.filter(
                GamePod.id == pod_id,
                GamePod.owner_user_id == current_user.id
            ).first()
            
            if not pod:
                continue
            
            # Get pod members without deck assignments
            for member in pod.members or []:
                if not member.roster_player:
                    continue
                
                roster_player = member.roster_player
                
                # Check if player already has deck assignments
                existing_assignments = len(roster_player.decks or [])
                if existing_assignments >= 2:  # Skip if already has enough decks
                    continue
                
                # Find available decks for this player
                if roster_player.user_id:
                    # User has account - assign their own decks
                    available_decks = (
                        db.session.query(Folder.id)
                        .join(FolderRole, FolderRole.folder_id == Folder.id)
                        .filter(
                            FolderRole.role.in_(FolderRole.DECK_ROLES),
                            Folder.owner_user_id == roster_player.user_id
                        )
                        .limit(3 - existing_assignments)
                        .all()
                    )
                    
                    for (deck_id,) in available_decks:
                        # Check if already assigned
                        existing = GameRosterDeck.query.filter(
                            GameRosterDeck.roster_player_id == roster_player.id,
                            GameRosterDeck.folder_id == deck_id
                        ).first()
                        
                        if not existing:
                            assignment = GameRosterDeck(
                                roster_player_id=roster_player.id,
                                owner_user_id=current_user.id,
                                folder_id=deck_id
                            )
                            db.session.add(assignment)
                            assignments_made += 1
                else:
                    # Guest player - assign from pod owner's collection
                    available_decks = (
                        db.session.query(Folder.id, Folder.name)
                        .join(FolderRole, FolderRole.folder_id == Folder.id)
                        .filter(
                            FolderRole.role.in_(FolderRole.DECK_ROLES),
                            Folder.owner_user_id == current_user.id
                        )
                        .limit(2 - existing_assignments)
                        .all()
                    )
                    
                    for deck_id, deck_name in available_decks:
                        assignment = GameRosterDeck(
                            roster_player_id=roster_player.id,
                            owner_user_id=current_user.id,
                            folder_id=deck_id
                        )
                        db.session.add(assignment)
                        assignments_made += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'assignments_made': assignments_made,
            'message': f'Auto-assigned {assignments_made} decks across {len(pod_ids)} pods'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


def api_quick_game_save():
    """API endpoint for saving quick game logs."""
    if request.method != 'POST':
        return jsonify({'error': 'Method not allowed'}), 405
    
    try:
        data = request.get_json()
        
        # Extract game data
        players = data.get('players', {})
        decks = data.get('decks', {})
        played_at = data.get('played_at')
        winner_seat = data.get('winner_seat')
        win_via_combo = data.get('win_via_combo', False)
        notes = data.get('notes', '').strip()
        
        if len(players) < 2:
            return jsonify({'error': 'At least 2 players required'}), 400
        
        # Create game session
        from models import GameSession, GameSeat, GamePlayer, GameDeck, GameSeatAssignment
        from datetime import datetime
        
        session = GameSession(
            owner_user_id=current_user.id,
            played_at=datetime.fromisoformat(played_at) if played_at else None,
            notes=notes or None,
            win_via_combo=bool(win_via_combo)
        )
        db.session.add(session)
        db.session.flush()
        
        # Create seats and assignments
        seats_by_number = {}
        for seat_num_str, player_data in players.items():
            seat_num = int(seat_num_str)
            
            # Create seat
            seat = GameSeat(
                session_id=session.id,
                seat_number=seat_num,
                turn_order=seat_num
            )
            db.session.add(seat)
            seats_by_number[seat_num] = seat
            
            # Create player
            player = GamePlayer(
                user_id=player_data.get('user_id'),
                display_name=player_data.get('name')
            )
            db.session.add(player)
            
            # Create deck
            deck_data = decks.get(seat_num_str, {})
            deck = GameDeck(
                session_id=session.id,
                folder_id=deck_data.get('folder_id'),
                deck_name=deck_data.get('name', 'Unknown Deck'),
                commander_name=deck_data.get('commander'),
                commander_oracle_id=deck_data.get('oracle_id')
            )
            db.session.add(deck)
            
            # Create assignment
            assignment = GameSeatAssignment(
                session_id=session.id,
                seat=seat,
                player=player,
                deck=deck
            )
            db.session.add(assignment)
        
        # Set winner if specified
        if winner_seat and int(winner_seat) in seats_by_number:
            session.winner_seat = seats_by_number[int(winner_seat)]
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'game_id': session.id,
            'message': 'Game logged successfully!'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# Add these to the service exports
__all__ = [
    "games_streamlined_players",
    "games_quick_log", 
    "api_quick_pod_create",
    "api_auto_assign_decks",
    "api_quick_game_save"
]