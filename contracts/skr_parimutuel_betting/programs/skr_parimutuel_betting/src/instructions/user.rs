use anchor_lang::prelude::*;
use anchor_spl::token::{Token, TokenAccount, Mint};
use crate::state::*;
use crate::errors::ContractError;

#[derive(Accounts)]
pub struct PlaceBet<'info> {
    #[account(
        seeds = [b"config"],
        bump,
    )]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub match_account: Account<'info, Match>,

    #[account(
        init,
        seeds = [b"bet", match_account.key().as_ref(), user.key().as_ref()],
        bump,
        payer = user,
        space = UserBet::SPACE
    )]
    pub user_bet: Account<'info, UserBet>,

    #[account(
        mut,
        associated_token::mint = skr_mint,
        associated_token::authority = user,
    )]
    pub user_skr_ata: Account<'info, TokenAccount>,

    #[account(
        mut,
        associated_token::mint = skr_mint,
        associated_token::authority = vault_authority
    )]
    pub vault_ata: Account<'info, TokenAccount>,
    
    /// CHECK: Validated against match_account directly
    #[account(
        seeds = [b"vault_auth", match_account.key().as_ref()],
        bump
    )]
    pub vault_authority: UncheckedAccount<'info>,

    pub skr_mint: Account<'info, Mint>,

    #[account(mut)]
    pub user: Signer<'info>,

    pub token_program: Program<'info, Token>,
    pub associated_token_program: Program<'info, anchor_spl::associated_token::AssociatedToken>,
    pub system_program: Program<'info, System>,
}

pub fn place_bet(ctx: Context<PlaceBet>, side: WinnerSide, amount: u64) -> Result<()> {
    let config = &ctx.accounts.config;
    let match_account = &mut ctx.accounts.match_account;

    require!(!config.paused, ContractError::SystemPaused);
    require!(match_account.status == MatchStatus::Open, ContractError::MatchNotOpen);
    require!(amount >= config.min_bet && amount <= config.max_bet, ContractError::BetOutOfRange);
    require!(side == WinnerSide::A || side == WinnerSide::B, ContractError::InvalidSide);
    require!(match_account.vault_ata == ctx.accounts.vault_ata.key(), ContractError::VaultAuthorityMismatch);
    require!(match_account.vault_authority == ctx.accounts.vault_authority.key(), ContractError::VaultAuthorityMismatch);

    // Transfer SKR from user to vault
    let cpi_transfer_accounts = anchor_spl::token::Transfer {
        from: ctx.accounts.user_skr_ata.to_account_info(),
        to: ctx.accounts.vault_ata.to_account_info(),
        authority: ctx.accounts.user.to_account_info(),
    };
    let cpi_transfer_ctx = CpiContext::new(
        ctx.accounts.token_program.to_account_info(),
        cpi_transfer_accounts,
    );
    anchor_spl::token::transfer(cpi_transfer_ctx, amount)?;

    // Record UserBet and tallies
    let user_bet = &mut ctx.accounts.user_bet;
    user_bet.match_pubkey = match_account.key();
    user_bet.user = ctx.accounts.user.key();
    user_bet.side = side.clone();
    user_bet.amount = amount;

    if side == WinnerSide::A {
        match_account.total_a = match_account.total_a.checked_add(amount).unwrap();
    } else {
        match_account.total_b = match_account.total_b.checked_add(amount).unwrap();
    }

    Ok(())
}

#[derive(Accounts)]
pub struct Claim<'info> {
    #[account(
        seeds = [b"config"],
        bump,
        has_one = skr_mint @ ContractError::TokenMintMismatch
    )]
    pub config: Box<Account<'info, Config>>,

    #[account(mut)]
    pub match_account: Box<Account<'info, Match>>,

    #[account(
        mut,
        close = user,
        has_one = user @ ContractError::Unauthorized,
        constraint = user_bet.match_pubkey == match_account.key() @ ContractError::Unauthorized
    )]
    pub user_bet: Box<Account<'info, UserBet>>,

    #[account(
        mut,
        associated_token::mint = skr_mint,
        associated_token::authority = user,
    )]
    pub user_skr_ata: Box<Account<'info, TokenAccount>>,

    #[account(
        mut,
        associated_token::mint = skr_mint,
        associated_token::authority = vault_authority
    )]
    pub vault_ata: Box<Account<'info, TokenAccount>>,
    
    /// CHECK: PDA
    #[account(
        mut,
        seeds = [b"vault_auth", match_account.key().as_ref()],
        bump
    )]
    pub vault_authority: UncheckedAccount<'info>,

    #[account(
        mut,
        token::mint = skr_mint
    )]
    pub treasury_ata: Box<Account<'info, TokenAccount>>,
    
    /// CHECK: Treasury wallet to match the global config
    #[account(mut)]
    pub treasury_wallet: UncheckedAccount<'info>,
    
    /// CHECK: To receive vault/match rent on auto-close
    #[account(
        mut,
        address = config.admin
    )]
    pub admin: UncheckedAccount<'info>,

    pub skr_mint: Box<Account<'info, Mint>>,

    #[account(mut)]
    pub user: Signer<'info>,

    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

pub fn claim(ctx: Context<Claim>) -> Result<()> {
    let match_account = &mut ctx.accounts.match_account;
    let config = &ctx.accounts.config;
    let user_bet = &ctx.accounts.user_bet;

    require!(match_account.status == MatchStatus::Resolved, ContractError::MatchNotResolved);
    require!(user_bet.side == match_account.winner, ContractError::NotWinner);
    require!(match_account.winning_total > 0, ContractError::WinningTotalZero); // Guard invariant
    require!(match_account.vault_ata == ctx.accounts.vault_ata.key(), ContractError::VaultAuthorityMismatch);
    require!(match_account.vault_authority == ctx.accounts.vault_authority.key(), ContractError::VaultAuthorityMismatch);

    // Verify treasury ATA derivation matches config global
    let expected_treasury_ata = anchor_spl::associated_token::get_associated_token_address(&config.treasury_wallet, &config.skr_mint);
    require!(ctx.accounts.treasury_ata.key() == expected_treasury_ata, ContractError::Unauthorized);

    // Payout Math
    let payout = ((match_account.payout_pool as u128)
        .checked_mul(user_bet.amount as u128).unwrap()
        .checked_div(match_account.winning_total as u128).unwrap()) as u64;

    let match_key = match_account.key();
    let signer_seeds: &[&[&[u8]]] = &[&[
        b"vault_auth",
        match_key.as_ref(),
        &[ctx.bumps.vault_authority],
    ]];

    if payout > 0 {
        let cpi_transfer_accounts = anchor_spl::token::Transfer {
            from: ctx.accounts.vault_ata.to_account_info(),
            to: ctx.accounts.user_skr_ata.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        let cpi_transfer_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_transfer_accounts,
            signer_seeds,
        );
        anchor_spl::token::transfer(cpi_transfer_ctx, payout)?;
    }

    match_account.claimed_winning_total = match_account.claimed_winning_total.checked_add(user_bet.amount).unwrap();

    // Auto-Close check
    if match_account.claimed_winning_total == match_account.winning_total {
        // Sweep remaining dust to treasury
        let _remaining_dust = ctx.accounts.vault_ata.amount.checked_sub(payout).unwrap_or(0); // payout already sent visually, but ATA amount object not refreshed, manual deduction needed or just reload context
        ctx.accounts.vault_ata.reload()?;
        let actual_remaining = ctx.accounts.vault_ata.amount;
        
        if actual_remaining > 0 {
            let cpi_sweep = anchor_spl::token::Transfer {
                from: ctx.accounts.vault_ata.to_account_info(),
                to: ctx.accounts.treasury_ata.to_account_info(),
                authority: ctx.accounts.vault_authority.to_account_info(),
            };
            let cpi_sweep_ctx = CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                cpi_sweep,
                signer_seeds,
            );
            anchor_spl::token::transfer(cpi_sweep_ctx, actual_remaining)?;
        }

        // Close vault ATA
        let cpi_close = anchor_spl::token::CloseAccount {
            account: ctx.accounts.vault_ata.to_account_info(),
            destination: ctx.accounts.admin.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        let cpi_close_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_close,
            signer_seeds,
        );
        anchor_spl::token::close_account(cpi_close_ctx)?;

        // Close Match PDA safely
        match_account.close(ctx.accounts.admin.to_account_info())?;
    }

    Ok(())
}

#[derive(Accounts)]
pub struct RefundBet<'info> {
    #[account(
        seeds = [b"config"],
        bump,
    )]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub match_account: Account<'info, Match>,

    #[account(
        mut,
        close = user,
        has_one = user @ ContractError::Unauthorized,
        constraint = user_bet.match_pubkey == match_account.key() @ ContractError::Unauthorized
    )]
    pub user_bet: Account<'info, UserBet>,

    #[account(
        mut,
        associated_token::mint = skr_mint,
        associated_token::authority = user,
    )]
    pub user_skr_ata: Account<'info, TokenAccount>,

    #[account(
        mut,
        associated_token::mint = skr_mint,
        associated_token::authority = vault_authority
    )]
    pub vault_ata: Account<'info, TokenAccount>,
    
    /// CHECK: PDA
    #[account(
        mut,
        seeds = [b"vault_auth", match_account.key().as_ref()],
        bump
    )]
    pub vault_authority: UncheckedAccount<'info>,

    /// CHECK: To receive vault/match rent on auto-close
    #[account(
        mut,
        address = config.admin
    )]
    pub admin: UncheckedAccount<'info>,

    pub skr_mint: Account<'info, Mint>,

    #[account(mut)]
    pub user: Signer<'info>,

    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

pub fn refund_bet(ctx: Context<RefundBet>) -> Result<()> {
    let match_account = &mut ctx.accounts.match_account;
    let user_bet = &ctx.accounts.user_bet;

    require!(match_account.status == MatchStatus::Cancelled, ContractError::MatchNotCancelled);
    require!(match_account.vault_ata == ctx.accounts.vault_ata.key(), ContractError::VaultAuthorityMismatch);
    require!(match_account.vault_authority == ctx.accounts.vault_authority.key(), ContractError::VaultAuthorityMismatch);

    let payout = user_bet.amount;
    let match_key = match_account.key();
    let signer_seeds: &[&[&[u8]]] = &[&[
        b"vault_auth",
        match_key.as_ref(),
        &[ctx.bumps.vault_authority],
    ]];

    if payout > 0 {
        let cpi_transfer_accounts = anchor_spl::token::Transfer {
            from: ctx.accounts.vault_ata.to_account_info(),
            to: ctx.accounts.user_skr_ata.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        let cpi_transfer_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_transfer_accounts,
            signer_seeds,
        );
        anchor_spl::token::transfer(cpi_transfer_ctx, payout)?;
    }

    match_account.refunded_total = match_account.refunded_total.checked_add(payout).unwrap();
    let total_bets = match_account.total_a.checked_add(match_account.total_b).unwrap();

    // Auto close check
    if match_account.refunded_total == total_bets {
        // Close vault ATA
        let cpi_close = anchor_spl::token::CloseAccount {
            account: ctx.accounts.vault_ata.to_account_info(),
            destination: ctx.accounts.admin.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        let cpi_close_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_close,
            signer_seeds,
        );
        anchor_spl::token::close_account(cpi_close_ctx)?;

        // Close Match PDA safely
        match_account.close(ctx.accounts.admin.to_account_info())?;
    }

    Ok(())
}

#[derive(Accounts)]
pub struct CloseLosingBet<'info> {
    #[account(
        seeds = [b"config"],
        bump,
    )]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub match_account: Account<'info, Match>,

    #[account(
        mut,
        close = admin,
        constraint = user_bet.match_pubkey == match_account.key() @ ContractError::Unauthorized
    )]
    pub user_bet: Account<'info, UserBet>,

    /// CHECK: Receives the SOL rent
    #[account(
        mut,
        address = config.admin
    )]
    pub admin: UncheckedAccount<'info>,

    #[account(mut)]
    pub payer: Signer<'info>, // Anyone can pay to execute this
}

pub fn close_losing_bet(ctx: Context<CloseLosingBet>) -> Result<()> {
    let match_account = &mut ctx.accounts.match_account;
    let user_bet = &ctx.accounts.user_bet;

    require!(match_account.status == MatchStatus::Resolved, ContractError::MatchNotResolved);
    require!(user_bet.side != match_account.winner, ContractError::NotLoser);

    // Track loser bet cleanup volume in branch-B (winning_total == 0).
    // This lets us close the match after the final loser bet PDA is closed.
    if match_account.winning_total == 0 {
        match_account.refunded_total = match_account.refunded_total.checked_add(user_bet.amount).unwrap();
        let total_bets = match_account.total_a.checked_add(match_account.total_b).unwrap();
        if match_account.refunded_total == total_bets {
            match_account.close(ctx.accounts.admin.to_account_info())?;
        }
    }

    // Anchor `close = admin` handles the loser bet lamports transfer + account deletion.
    
    Ok(())
}
